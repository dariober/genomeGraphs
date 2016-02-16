#!/usr/bin/env python

import argparse
import sys
import os
import re
import tempfile
import shutil
import glob
import time
import pycoverage
import pympileup
import validate_args
import pybedtools
import atexit
import gzip

#from genomeGraphs.pycoverage import *
#from genomeGraphs.validate_args import *
#import genomeGraphs.pympileup
##import genomeGraphs

VERSION= '0.3.1' ## MAKE IT MATCH setup.py

# HOWTO: Add recyclable graphical parameters
# ------------------------------------------
# E.g. we want to add a new col parameter that has to be applied to each track.
# Recycled if not enough items are passed to command line.
# 
# 1. Add arguments to argparse parser. Use nargs= '+' to make it a list:
#   p.add_argument('--col_line', default= ['blue'], nargs= '+', ...)
# 
# 2. Add this arguments to RPlot function. Enclose the list in pycoverage.quoteStringList.
#   This will send the arg to the R script:
# RPlot(..., col_line= pycoverage.quoteStringList(args.col_line), ...)
#
# 3. In R_template.R: Get this argument by assigning to a var and recycle as
#    necessary. col_line is now a vector c('blue', 'red', ...):
# plot_paramas$col_line<- recycle(nrow(pot_params), c(%(col_line)s))
#
# 4. Add this parameter to the list `allowed_args` in  pycoverage.read_parfile()

parser = argparse.ArgumentParser(description= """

DESCRIPTION

    genomeGraphs is a command line oriented genome viewer.

    It produces pdf files of coverage plots for one or more bam/bedgraph/gtf/bed
    files at the intervals specified in a bed file. 
    
    Plots can be annotated according to a gtf or bed file and decorated with the
    individual nucleotides if a corresponding reference FASTA file is provided.
    
    Intermediate output files, including the R script, can be saved for future
    inspection.

EXAMPLE:

    Plot coverage of all the bam files in current dir in the region(s) in file
    actb.bed.
    
        genomeGraphs -i *.bam -b actb.bed

SEE ALSO:
    
    Documentation at
    http://code.google.com/p/bioinformatics-misc/wiki/coverage_screenshots_docs
    """, prog= 'genomeGraphs', formatter_class= argparse.RawDescriptionHelpFormatter) # , formatter_class= argparse.RawTextHelpFormatter

# -----------------------------------------------------------------------------

parser.add_argument('--version', action='version', version='%(prog)s '  + VERSION)

input_args= parser.add_argument_group('Input options', '')

input_args.add_argument('--ibam', '-i',
                   required= False,
                   default= [],
                   nargs= '+',
                   help='''List of input files for which coverage or annotation
should be plotted. Bam files must be sorted and indexed. bedGraph files are
taken as coverage all the other formats (bed, gtf, generic txt) are "annotation".
Input can be gzipped. Metacharacters are expanded by python (`glob`). E.g. to
match all the bam files use '*.bam'. Use '-' to read the list of files from stdin.
                   ''')

input_args.add_argument('--bed', '-b',
                   required= True,
                   help='''Bed or Gtf file with regions to plot optionally gzipped.
Use - to read from stdin.
                   ''')

input_args.add_argument('--slop', '-s',
                   default= ['0.05', '0.05'],
                   nargs= '+',
                   help='''One or two integeres or floats to extend (zoom-out) each
interval in --bed input. If integer(s), extend by that many bases left and/or right.
If float(s), extend by that percent of interval size (e.g. 0.1 to extend by 10%%).
If one value is given, it will be applied to left and right. If two values, first
will be applied to left and second to right. Must be >= 0.
                   ''')


input_args.add_argument('--fasta', '-f',
                   help='''Fasta file of the reference genome. If given, high
resolution plots will show the reference bases at each position.
                   ''')

input_args.add_argument('--samtools',
                    default= '',
                    help='''Path to samtools. Default is '' which assumes it is
on PATH''')

input_args.add_argument('--parfile', '-pf',
                    default= None,
                    help='''_In prep_: Parameter file to get arguments from.''')

input_args.add_argument('--sorted',
                    action= 'store_true',
                    help='''Use bedtools' "chromsweep" algorithm to input -i
sorted by position `sort -k1,1 -k2,2n`. Note that the -b input is always internally (re)sorted
by bedtools.''')


# -----------------------------------------------------------------------------
output_args = parser.add_argument_group('Output options', '')

output_args.add_argument('--outdir', '-d',
                   required= False,
                   default= None,
                   help='''Output directory for the pdf files. Default to current
dir. NB: Not to be confused with --tmpdir where working files go.
                   ''')

output_args.add_argument('--onefile', '-o',
                   required= False,
                   default= None,
                   help='''If given, concatenate all the pdf figures into this
output file (requires PyPDF2 package). 
                   ''')

output_args.add_argument('--tmpdir', '-t',
                    default= None,
                    help='''Directory where to dump temporary files. If not assigned
the tmpdir will be deleted. If set it will *not* be deleted.
                   ''')

output_args.add_argument('--nwinds', '-w',
                   type= int,
                   default= 1000,
                   help='''Maximum number of data-points to plot. If the bed interval
contains more than --nwinds data points it will be divided into equally sized
windows and the counts or bedgraph scores grouped by window. Small value give a coarse
resolution while larger values more jagged profile. Default 1000.
If nwinds < maxseq,  nwinds is reset to maxseq.  
''')

output_args.add_argument('--replot', 
                   action= 'store_true',
                   help='''Re-draw plots using the output files from a previous
execution. This option allows to reformat the plots without going thorugh the time
consuming steps of generating pileups, intersections etc.
''')

output_args.add_argument('--rpm',
                   action= 'store_true',
                   help='''Normalize counts by reads per million using library
sizes. Default is to use raw counts. (Only relevant to bam files).
''')

output_args.add_argument('--verbose', '-v',
                   action= 'store_true',
                   help='''Print verbose output. Currently this option only adds
the stdout and stderr from R. Only useful for debugging.
''')

output_args.add_argument('--group_fun',
                   type= str,
                   default= 'mean',
                   help='''The function to group-by windows if the bedgraph or bam files
generate ranges larger than --nwinds. Default 'mean'. See bedtools groupby for
valid options.
''')

# -----------------------------------------------------------------------------
plot_coverage= parser.add_argument_group('Coverage options', '''
These options affect all and only the coverage tracks''')

plot_coverage.add_argument('--maxseq', '-m',
                    default= 100,
                    type= int,
                    help='''The maximum width of the region (bp) to print bases
and to plot each nucleotide in different colour. Default 100 (i.e. regions smaller
than 100 bp will be printed with colour coded nucleotides and the sequence will
be shown).''')

plot_coverage.add_argument('--col_nuc', nargs= '+', default= [''], help='''Colour for the four
nucleotides. _in prep_ File to the colour
code for counts of the ACTGN (counts on + strand) and actgn (counts on - strand).
''')

plot_coverage.add_argument('--no_col_bases', action= 'store_true', help='''Do not colour
individual bases even if the span of the region is < maxseq. I.e. use col_track
and col_track_rev
''')

plot_coverage.add_argument('--col_all', '-c',
                    action= 'store_true',
                    help='''Paint each bar with the colours given in --col_nuc
even if the base matches the reference. Default is to paint only mismatching bases.
Irrelvant if --maxseq is exceeded or a reference is not given to --fasta.
                   ''')

# -----------------------------------------------------------------------------
annotation_args= parser.add_argument_group('Track options', '''
Graphical options to draw coverage and annotation profiles. Multiple arguments
recycled. E.g. `--col_line blue red` to make blue tracks 1st, 3rd, 5th, ... and
red tracks 2nd, 4th, 6th, ...''')

annotation_args.add_argument('--col_line', default= ['darkblue'], nargs= '+',
    help='''Colour of the profile line of each coverage track. Default blue''')
annotation_args.add_argument('--lwd', default= [0.01], type= float, nargs= '+',
    help='''Line width of the profile line of each coverage track''')
annotation_args.add_argument('--cex', default= 1, type= float,
                             help='''Character expansion for all text. All the other
cex parameters will be based on this''')
annotation_args.add_argument('--col_text_ann', default= ['black'], nargs= '+', help='''Colour for annotation text (gene names)''')
annotation_args.add_argument('--col_track', default= [''], nargs= '+', help='''Colour for coverage and annotation tracks and for the N base.
Default will assign grey to coverage and firebrick4 to annotation''')
annotation_args.add_argument('--col_track_rev', default= [''], nargs= '+', help='''Relevant to bam files only: Colour for reads on reverse strand. Use NA
to use the same colour as for forward reads (i.e. turn it off)''')

annotation_args.add_argument('--rcode', default= [''], nargs= '+',
    help='''A list of strings, one for each plot and recycled, that will be evaluated as R code at the end of each plot.
Useful to annotate plots. E.g. "abline(h= 10)". Enclose each string in double-quotes and use single quotes for the R code inside (To be fixed).''')

# -----------------------------------------------------------------------------
plot_layout= parser.add_argument_group('Plot layout', '''
Most of these options take one argument per plot with arguments recyled if not
enough. E.g. `--ylab Count RPM` to label "Count" the 1st, 3nd, ... plot and "RPM"
the 2nd, 4th ... plot''')

plot_layout.add_argument('--ymax', '-Y',
                    default= ['indiv'],
                    type= str,
                    nargs= '+',
                    help='''Maximum limit of y-axis. Options are:
'indiv': Scale each plot individually to its maximum (default).
'max' all y-axes set to the maximum value of all the coverage plots.
<float>: Set all the plots to this maximum.
'max' cannot be combined with other choices. Float and indiv will be recycled.
                   ''')

plot_layout.add_argument('--ymin', '-y',
                    default= ['min'],
                    type= str,
                    nargs= '+',
                    help='''Minimum limit of y-axis. Options are:
'min' (default) all y-axes set to 0 or the minimum value of all the coverage plots.
'indiv': Scale each plot individually to its minimum.
<float>: Set all the plots to this minimum.''')

plot_layout.add_argument('--ylab', '-yl',
                    default= [''],
                    type= str,
                    nargs= '+',
                    help='''Labels for Y-axis. Recycled.''')

plot_layout.add_argument('--cex_lab', '-cl',
                    default= [-1],
                    type= float,
                    nargs= '+',
                    help='''Character expansion for labels of Y-axis. Recycled. Default to same as cex_axis.''')

plot_layout.add_argument('--col_yaxis',
                    default= ['black'],
                    nargs= '+',
                    help='''Colour for annotationa of y-axis line and figures. Default black''')

plot_layout.add_argument('--vheights', '-vh',
                    default= [''],
                    type= str,
                    nargs= '+',
                    help='''List of proportional heights to be passed to R layout(). Recycled.
E.g. if `4 2 1` will make the 1st track twice the height of the 2nd and 4 times the height of 3rd.''')

plot_layout.add_argument('--mar_heights', '-mh',
                    default= [-1, -1],
                    type= float,
                    nargs= 2,
                    help='''List of two proportional heights to assign to the top panel (where region name
is printed) and bottom panel (where you have chromosome position, range, sequence)
E.g. if `--vheights 4` and `--mar_heights 1 2` the top panel is 1/4th the annotation track and
the bottom 1/2. Negative values will switch to default.''')


plot_layout.add_argument('--names', default= None, nargs= '+', help='''List of names for the samples. Default ('') is to use the names of the
input files. Recycled as necessary.''')
plot_layout.add_argument('--cex_names', default= 1, type= float, help='''Character exapansion for the names of the samples''')
plot_layout.add_argument('--col_names', default= ['#0000FF50'], nargs= '+',
    help='''List of colours for the name of each samples. Colours recycled as necessary.
Useful to colour-code samples according to experimemtal design.''')

plot_layout.add_argument('--bg', nargs= '+', default= ['white'],
    help='''List of colours for the plot backgrounds. Recycled as necessary.''')

plot_layout.add_argument('--col_grid', default= ['darkgrey'], nargs= '+',
    help='''Grid colour. Recycled.''')

plot_layout.add_argument('--col_mark', default= ['red'], nargs= '+', help='''Colour for the two symbols (triangles) marking the limits of the bed region.
    Default red. Recycled''')

plot_layout.add_argument('--overplot', '-op', nargs= '+', default= ['NA'], type= int,
    help='''List of integers to identify *coverage* tracks to be drawn on the same plot.
Annotation tracks are not affected. Recycled. E.g. to have the 1st and 3rd input
file on the same plot and the 2nd and 4th on the same plot, use `-op 1 2 1 2`''')

# -----------------------------------------------------------------------------

xaxis_args= parser.add_argument_group('Annotation of x-axis', '''
Affect x-axis labelling, range and sequence of interval''')

xaxis_args.add_argument('--cex_axis', default= 1, type= float, help='''Character exapansion for the axis annotation.''')
# xaxis_args.add_argument('--cex_range', default= 1, type= float, help='''Character exapansion for the range of the x-axis''')
xaxis_args.add_argument('--cex_seq', default= 1, type= float, help='''Character exapansion for the nucleotide sequence''')
xaxis_args.add_argument('--col_seq', default= 'black', help='''Colour for the nucleotide sequence.''')

# -----------------------------------------------------------------------------
figure_size_args= parser.add_argument_group('Global graphical options', '''
These options the output figure as a whole''')

figure_size_args.add_argument('--title', default= None,
    help= '''Title for each region. Default are the region coords <chrom>:<start>-<end>
You can add a custom title while keeping the coordinates using the syntax --title ':region: My title'
TODO: Make it recyclable/one for each region?''')

figure_size_args.add_argument('--cex_title', default= 1, type= float,
    help= '''Character expansion for --title. This is independent to --cex.''')

figure_size_args.add_argument('--fbg', default= 'grey85',
    help='''Colour for the whole figure background. Default grey85''')

figure_size_args.add_argument('--mar', default= 4, type= float, help='''Spacing on the left margin in line numbers. Default 4.
Increase or decrease to give space to y-axis labels.''')

figure_size_args.add_argument('--pwidth', '-W',
                    default= 15,
                    type= float,
                    help='''Width of the plots in cm. Default 15
                    ''')

figure_size_args.add_argument('--pheight', '-H',
                    default= -1,
                    type= float,
                    help='''Height of the figure in cm
                   ''')

figure_size_args.add_argument('--psize', '-p',
                    default= 10,
                    type= float,
                    help='''Pointsize for R pdf() function. Sizes
between 9 and 12 should suite most cases. Default 10.
                   ''')

# -----------------------------------------------------------------------------
# END ARGPARSE
# -----------------------------------------------------------------------------

def main():
    args = parser.parse_args()
    if args.parfile:
        pf= pycoverage.read_parfile(args.parfile)
        if not pf:
            sys.exit('Error parsing parameter file %s' %(args.parfile))
        args= pycoverage.assign_parfile(pf, args)
    # Checking arguments
    # ------------------
    if args.ibam == '-' and args.bed == '-':
        sys.exit('stdin passed to *both* --ibam and --bed!')
    try:
        assert validate_args.validate_ymax(args.ymax)
        assert validate_args.validate_ymin(args.ymin)
    except AssertionError:
        print('''Invalid arguments passed to ymax (%s) or ymin (%s).''' %(args.ymax, args.ymin))
        sys.exit(1)
    if len(args.slop) > 2:
        print('Only one or two ints or floats must be passed to --slop! Got %s' %(args.slop))
        sys.exit(1)
    if len(args.slop) == 1:
        args.slop.append(args.slop[0])
    slop= []
    try:
        slop.append(int(args.slop[0]))
    except ValueError:
        try:
            slop.append(float(args.slop[0]))
        except:
            sys.exit('Invalid argument passed to --slop. Must be ints or floats: %s' %(args.slop))
    try:
        slop.append(int(args.slop[1]))
    except ValueError:
        try:
            slop.append(float(args.slop[1]))
        except:
            sys.exit('Invalid argument passed to --slop. Must be ints or floats: %s' %(args.slop))
            
    if args.nwinds < args.maxseq:
        print('\nWarning: nwinds (%s) reset to maxseq (%s)' %(args.nwinds, args.maxseq))
        nwinds= args.maxseq
    else:
        nwinds= args.nwinds
    if args.replot and args.tmpdir is None:
        sys.exit('\nCannot replot without a working (--tmpdir) directory!\n')
    if args.ibam == ['-']:
        inputlist_all= [x.strip() for x in sys.stdin.readlines()]
    else:
        inputlist_all= pycoverage.getFileList(args.ibam)
    inputlist= pycoverage.dedupFileList(inputlist_all)
    bamlist= [x for x in inputlist if x.endswith('.bam')]
    nonbamlist= [x for x in inputlist if not x.endswith('.bam')]
    names= validate_args.parse_names(args.names, inputlist_all)
    if not args.replot:
        print('\nFiles to analyze (%s found):\n%s\n' %(len(inputlist), ', '.join(inputlist)))
    if len(inputlist) == 0 and not args.replot:
        sys.exit('No file found!\n')
    
    # Output settings
    # ---------------
    if (args.outdir is not None) and (args.onefile is not None):
        sys.exit('''\nSpecify either --outdir (for one file for each bed region) OR
--onefile (for one single concatenated file).\n''')
    
    onefile= False
    if args.outdir is None and (args.onefile is None):
        outdir= os.getcwd()
    elif args.onefile is None:
        outdir= args.outdir
        if not os.path.exists(outdir):
            os.makedirs(outdir)
    else:
        onefile= True
        outdir= os.path.split(args.onefile)[0]
        try:
            import PyPDF2
        except ImportError:
            sys.exit('''\nModule PyPDF2 could not be imported. Eiher installed it
(see https://pypi.python.org/pypi/PyPDF2 ) or avoid using the --onefile option.\n''')
            
    ## Temp dir to dump intermediate files.
    ## -------------------------------------------------------------------------
    if args.tmpdir is None:
        tmpdir= tempfile.mkdtemp(suffix= '_coverageViewer')
        atexit.register(shutil.rmtree, tmpdir)
    else:
        tmpdir= args.tmpdir
        if not os.path.exists(tmpdir):
            os.makedirs(tmpdir)
    
    outputPDF= [] ## List of all the pdf files generated. Used only for --onefile
        
    ## -------------------------------------------------------------------------
    if args.rpm and not args.replot:
        sys.stdout.write('Getting library sizes... ')
        libsizes_dict= pympileup.getLibrarySizes(bamlist, args.samtools)
        libsizes= [libsizes_dict[x] for x in bamlist]
        print(', '.join([str(x) for x in libsizes]))
    if args.bed == '-':
        inbed= sys.stdin
        fh= pycoverage.stdin_inbed_to_fh(inbed)
        inbed= open(fh.name)
        os.remove(fh.name)
    elif args.bed.endswith('.gz'):
        inbed= gzip.open(args.bed)
    else:
        inbed= open(args.bed)
    inbed= pybedtools.BedTool(inbed).sort() ## inbed is args.bed file handle
    
    # ---------------------[ Pre-filter non-bam files ]-------------------------
    
    xinbed= pybedtools.BedTool(inbed).each(pycoverage.slopbed, slop).sort().merge().saveas()
    ## BigWigs: Pass them through bigWigToBedGraph.py and replace the output name
    ## in nonbamlist. exts: .bw, .bigWig, .bigwig 
    nonbam_dict= {}
    for nonbam in nonbamlist:
        print('Pre-parsing %s' %(nonbam))
        nonbam_dict[nonbam]= pycoverage.prefilter_nonbam_multiproc(nonbam= nonbam, inbed= xinbed, tmpdir= tmpdir, sorted= args.sorted)

    # -----------------------[ Loop thorugh regions ]----------------------------
    for region in inbed:
        print('Processing: %s' %(str(region).strip()))
        bstart= region.start
        bend= region.end
        regname= '_'.join([str(x) for x in [region.chrom, bstart, bend]])
        if region.name != '' and region.name != '.':
            regname = regname + '_' + region.name
        regname= re.sub('[^a-zA-Z0-9_\.\-\+]', '_', regname) ## Get rid of metachar to make sensible file names
        xregion= pycoverage.slopbed(region, slop)
        
        ## --------------------[ Prepare output file names ]-------------------
        
        fasta_seq_name= os.path.join(tmpdir, regname + '.seq.txt')
        if bamlist != []:
            mpileup_name= os.path.join(tmpdir, regname + '.mpileup.bed.txt')
            mpileup_grp_name= os.path.join(tmpdir, regname + '.grp.bed.txt')
        else:
            mpileup_name= ''
            mpileup_grp_name= ''
        if nonbamlist != []:
            non_bam_name= os.path.join(tmpdir, regname + '.nonbam.bed.txt')
        else:
            non_bam_name= ''
        pdffile= os.path.join(tmpdir, regname + '.pdf')
        final_pdffile= os.path.join(outdir, regname + '.pdf')
        rscript= os.path.join(tmpdir, regname + '.R')
        if not args.replot:
            pycoverage.prepare_reference_fasta(fasta_seq_name, args.maxseq, xregion, args.fasta) ## Create reference file even if header only
            ## ----------------------- BAM FILES -------------------------------
            ## At the end of this session you have *.grp.bed.txt (matrix-like
            ## file read by R)
            regionWindowsDone= False ## In previous versions this var was regionWindows itself.
                                    ## However just checking `if regionWindows`
                                    ## consumes a file handle which is never closed!!
            if bamlist != []:
                if not regionWindowsDone:
                    regionWindows= pycoverage.makeWindows(xregion, nwinds) ## bed interval divided into nwinds intervals by bedtools windowMaker
                    regionWindowsDone= True
                pympileup.bamlist_to_mpileup(mpileup_name, mpileup_grp_name,
                    bamlist, xregion, nwinds, args.fasta, args.rpm, regionWindows,
                    samtools= args.samtools, groupFun= args.group_fun) ## Produce mpileup matrix
            else:
                mpileup_grp_name= ''
            
            ## ----------------------NON BAM FILES -----------------------------
            ## Produce coverage and annotation files for non-bam files. One output
            ## file prooduced with format
            ## chrom, start, end, file_name, A, C, G, T, Z.
            ## NB: A,C,G,T are always NA. We keep them only for compatibility
            ## with the output form BAM files. The `score` or `name` column from
            ## bed files (4th) goes to column Z.
            ## file_name has the name of the file as it has been passed to --ibam
            if nonbamlist != []:
                non_bam_fh= open(non_bam_name, 'w') ## Here all the files concatenated.
                for x in nonbamlist:
                    nonbam= nonbam_dict[x]
                    if x.endswith('.bedGraph') or x.endswith('.bedGraph.gz'):
                        """Bedgraph needs to go to tmp file because you don't know if
                        it has to be compressed by windows or not. <- This should be
                        changed: You have already intersected the nonbam files with
                        the bed regions.
                        """
                        tmpfh= tempfile.NamedTemporaryFile(dir= tmpdir, suffix= 'nonbam.tmp.bed', delete= False)
                        tmp_name= tmpfh.name
                        nlines= pycoverage.prepare_nonbam_file(nonbam, tmpfh, xregion, use_file_name= x) ## Write to fh the overlaps btw nonbam and region. Return no. lines
                        tmpfh.close()
                        if nlines > nwinds:

                            if not regionWindowsDone:
                                regionWindows= pycoverage.makeWindows(xregion, nwinds)
                                regionWindowsDone= True
                            pycoverage.compressBedGraph(regionWindows, tmp_name, use_file_name= x, bedgraph_grp_fh= non_bam_fh, col_idx= 4 + len(pympileup.COUNT_HEADER),
                                groupFun= args.group_fun)
                        else:
                            fh= open(tmp_name)
                            for line in fh:
                                non_bam_fh.write(line)
                        os.remove(tmp_name)
                    else:
                        nlines= pycoverage.prepare_nonbam_file(nonbam, non_bam_fh, xregion, use_file_name= x) ## Write to fh the overlaps btw nonbam and region. Return no. lines
                non_bam_fh.close()
            else:
                non_bam_name= ''
        # ----------------------------------------------------------------------
        # Plotting 
        # ----------------------------------------------------------------------
        outputPDF.append(pdffile)
        rgraph= pycoverage.RPlot(
              inputlist= pycoverage.quoteStringList(inputlist_all),
              count_header= pycoverage.quoteStringList(pympileup.COUNT_HEADER),
              pdffile= pdffile,
              rscript= rscript,
              mcov= mpileup_grp_name,
              nonbam= non_bam_name,
              refbases= fasta_seq_name,
              title= args.title,
              cex_title= args.cex_title,
              pheight= args.pheight,
              pwidth= args.pwidth,
              psize= args.psize,
              ylab= pycoverage.quoteStringList(args.ylab),
              cex_lab= pycoverage.quoteStringList(args.cex_lab),
              col_yaxis= pycoverage.quoteStringList(args.col_yaxis),
              bstart= bstart,
              bend= bend,
              xlim1= xregion.start,
              xlim2= xregion.end,
              maxseq= args.maxseq,
              ymax= pycoverage.quoteStringList(args.ymax),
              ymin= pycoverage.quoteStringList(args.ymin),
              chrom= xregion.chrom,
              vheights= pycoverage.quoteStringList(args.vheights),
              mar_heights= pycoverage.quoteStringList(args.mar_heights),
              cex= args.cex,
              cex_axis= args.cex_axis,
              col_mark= pycoverage.quoteStringList(args.col_mark),
              col_line= pycoverage.quoteStringList(args.col_line),
              lwd= pycoverage.quoteStringList(args.lwd),
              col_track= pycoverage.quoteStringList(args.col_track),
              col_track_rev= pycoverage.quoteStringList(args.col_track_rev),
              col_nuc= pycoverage.quoteStringList(args.col_nuc),
              no_col_bases= args.no_col_bases,
              bg= pycoverage.quoteStringList(args.bg),
              fbg= args.fbg,
              col_grid= pycoverage.quoteStringList(args.col_grid),
              col_text_ann= pycoverage.quoteStringList(args.col_text_ann),
              names= pycoverage.quoteStringList(names),
              col_names= pycoverage.quoteStringList(args.col_names),
              cex_names= args.cex_names,
              # cex_range= args.cex_range,
              cex_seq= args.cex_seq,
              col_seq= args.col_seq,
              mar= ', '.join([str(x) for x in [0, args.mar, 0.2, 1]]),
              col_all= args.col_all,
              rcode= pycoverage.quoteStringList(args.rcode),
              overplot= pycoverage.quoteStringList(args.overplot)
              )
        
        if rgraph['returncode'] != 0:
            print('\ngenomeGraphs.py: Exception in executing R script "%s"; returncode: %s\n' %(rscript, rgraph['returncode']))
            print('** Captured stdout:')
            print(rgraph['stdout'])
            print('** Captured stderr:')
            sys.exit(rgraph['stderr'])
        if args.verbose:
            print(rgraph['stderr'])
            print(rgraph['stdout'])
        if not onefile and tmpdir != outdir:
            ## Copy PDFs from temp dir to output dir. Unless you want them in onefile or
            ## if the final destination dir has been set to be also the tempdir
            shutil.copyfile(pdffile, final_pdffile)
    if onefile:
        pycoverage.catPdf(in_pdf= outputPDF, out_pdf= args.onefile)
    for f in nonbam_dict:
        os.remove(nonbam_dict[f])
#    if args.tmpdir is None:
#        shutil.rmtree(tmpdir)
if __name__ == '__main__':
    main()
    sys.exit()
