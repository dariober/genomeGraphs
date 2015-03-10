## Download

### Download repository 

With svn

    svn co https://github.com/dariober/genomeGraphs
    cd genomeGraphs/trunk/genomeGraphs

Or download source [genomeGraphs-master.zip](https://github.com/dariober/genomeGraphs/archive/master.zip), unzip and change directory to genomeGraphs

    unzip genomeGraphs-master.zip
    cd genomeGraphs-master/genomeGraphs

### Install

Optional, typically not required: delete previous installation build

    python setup.py clean --all

User specific installation. No need of admin rights:

    python setup.py install --user --install-scripts $HOME/bin/

Or install for all users, root access required:

    python setup.py install

## See also

To view help and and options

    genomeGraphs -h

Former wiki on [Google code](http://code.google.com/p/bioinformatics-misc/wiki/coverage_screenshots_docs) and current wiki on [GitHub](https://github.com/dariober/genomeGraphs/wiki)
