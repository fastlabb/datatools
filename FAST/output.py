#!/usr/bin/env python
#
# Helper module for postprocessing FAST outputs
# Tested with OpenFAST v8.17.00a-bjj
#
# Mostly legacy code at this point. Original behavior can be recovered
# with 'default_aliases' and 'verbose' set to True. Otherwise, typical
# usage:
#
#   from datatools.FAST.outputs import read
#   fst = read('Turbine.out')
#   df = fst.to_pandas()
#
# Written by Eliot Quon (eliot.quon@nrel.gov) 2017-07-31
#
from __future__ import print_function
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd

def read(outputFile,**kwargs):
    return FASToutput(outputFile,**kwargs)

def read_channels(sumfile,maxlines=9999):
    """Read list of channels from summary file, useful if output is
    binary and the channel information is not available.
    """
    with open(sumfile,'r') as f:
        Nread = 0
        line = f.readline()
        while (Nread < maxlines) and \
                (not line.lstrip().startswith('Requested Channels')):
            Nread += 1
            line = f.readline()
        for _ in range(3):
            f.readline() # channel header lines
        channel_number = []
        channel_name = []
        channel_units = []
        channel_source = []
        line = f.readline()
        while not line =='':
            items = line.strip().split()
            channel_number.append(items[0])
            channel_name.append(items[1])
            channel_units.append(items[2])
            channel_source.append(items[3])
            try:
                line = f.readline()
            except IOError:
                break
    return channel_name,channel_units,channel_source

class FASToutput(object):
    # TODO: override dict instead of generic object

    def __init__(self,fname,binary=False,default_aliases=False,verbose=False):
        # inputs
        self.fname = fname
        self.binary = binary
        self.verbose = verbose
        # initialize members
        self.outputs = None
        self.units = None
        self.output_units = dict()
        self.Noutputs = 0
        self.N = 0
        # read output file
        self.default_aliases = default_aliases
        self._readFASToutput(fname)
        if verbose:
            self.printStats()

    def __getitem__(self, key):
        if key in self.outputs:
            return getattr(self, key)
        else:
            raise KeyError('Requested key \'{:s}\' not in {}'.format(key,self.outputs))

    def _readASCII(self,fname,Nheaderlines):
        # first read header
        if self.verbose: print('Reading header info from',fname)
        with open(fname,'r') as f:
            if self.verbose:
                for _ in range(Nheaderlines): print(f.readline().strip())
            else:
                for _ in range(Nheaderlines): f.readline()
            # read names of output quantities
            self.outputs = f.readline().split()
            # read units for each output quantity
            self.units = [ s.strip('()') for s in f.readline().split() ]
            self.Noutputs = len(self.outputs)
            assert(self.Noutputs == len(self.units))
            for iline,_ in enumerate(f): pass
        self.N = iline + 1
        # then open file again to read the data
        if self.verbose: print('Reading data...')
        return np.loadtxt(fname,skiprows=Nheaderlines+2)

    def _readBinary(self,fname):
        from datatools.binario import BinaryFile
        print('Not implemented')

    def _readFASToutput(self,fname,Nheaderlines=6):
        # read data
        if self.binary:
            data = self._readBinary(fname)
        else:
            data = self._readASCII(fname,Nheaderlines)
        # set data columns as attributes
        for i,output in enumerate(self.outputs):
            setattr(self,output,data[:,i])
            self.output_units[output] = self.units[i]
        assert(len(self.Time) == self.N)
        # setup default aliases for convenience
        if self.default_aliases:
            self._setAlias('t','Time')
            self._setAlias('P','RotPwr')
            self._setAlias('T','RotThrust','LSShftFxa','LSShftFxs','LSSGagFxa','LSSGagFxs')
            self._setAlias('rpm','LSSTipVxa')
            self._setAlias('genspd','HSShftV')
            self._setAlias('pitch1','PtchPMzc1')
            self._setAlias('pitch2','PtchPMzc2')
            self._setAlias('pitch3','PtchPMzc3')
            self._setAlias('pitch','pitch1')

    def addOutput(self,name,data,units=None):
        if name not in self.outputs:
            self.outputs.append(name)
            setattr(self,name,data)
            if units is not None: self.output_units[name] = units
        else:
            print('Output',name,'already exists')

    def _setAlias(self,name,*aliases):
        for alias in aliases:
            try:
                data = getattr(self,alias)
            except AttributeError:
                continue
            else:
                self.addOutput(name, data)
                if self.verbose:
                    print('  set',name,'-->',alias)
                return
        if self.verbose:
            print('Outputs for alias',name,'do not exist:',aliases)

    def printStats(self):
        print('Output       Units    Min          Max          Mean         Stdev')
        print('------------ -------- ------------ ------------ ------------ ------------')
        for output,units in zip(self.outputs,self.units):
            data = getattr(self,output)
            print('{:12s} {:8s} {:12g} {:12g} {:12g} {:12g}'.format(
                        output,
                        units,
                        np.min(data),
                        np.max(data),
                        np.mean(data),
                        np.std(data)
                    ))
        print('')

    def plot(self,outputName,*args,**kwargs):
        data = getattr(self,outputName)
        Ndata = len(data)
        Nt = len(self.t)
        if Ndata == Nt:
            time = self.t
        elif Ndata < Nt:
            Navg = Nt - Ndata
            time = self.t[Navg/2:-Navg/2]
        plt.plot(time, data, *args, **kwargs)


    def running_mean(self,outputName,Tavg):
        N = int(Tavg / (self.t[1]-self.t[0]))
        data = getattr(self,outputName)
        mean = np.convolve(data, np.ones((N,))/N, mode='valid')
        newname = outputName + '_mean'
        self.addOutput(newname,mean)
        print('Averaged {:s} with {:f} s window (N={:d})'.format(outputName,self.t[N+1]-self.t[0],N))
        return mean


    def low_pass_filtered_mean(self,outputName,fc=np.inf,order=2):
        # Example: https://gist.github.com/junzis/e06eca03747fc194e322
        from scipy.signal import butter, lfilter
        data = getattr(self,outputName)
        fs = 1./(self.t[1] - self.t[0])
        cutoff_norm = fc / (0.5*fs) # Wn normalized from 0 to 1, where 1 is the Nyquist frequency
        b,a = butter(order, cutoff_norm, btype='lowpass', analog=False, output='ba')
        filtered_data = lfilter(b, a, data)
        newname = outputName + '_mean'
        self.addOutput(newname,filtered_data)
        print('Filtered {:s} with cutoff freq={:f} Hz, order={:d}'.format(outputName,fc,order))
        return filtered_data


    def fluctuations(self,outputName,meanName=None):
        if meanName is None:
            meanName = outputName + '_mean'
            if not hasattr(self,meanName):
                self.low_pass_filtered_mean(outputName)
        data = getattr(self,outputName)
        mean = getattr(self,meanName)
        if len(data) > len(mean):
            Navg = len(data) - len(mean)
            data = data[Navg/2:-Navg/2]
        fluc = data - mean
        newname = outputName + '_fluc'
        self.addOutput(newname,fluc)
        return fluc


    def vector_magnitude(self,outputname,*components):
        mag_sq = 0.0
        for comp in components:
            mag_sq += self[comp]**2
        units = self.output_units[components[0]]
        self.addOutput(outputname, mag_sq**0.5, units=units) 


    def to_pandas(self):
        """Return FAST output as a dataframe"""
        df = pd.DataFrame()
        for output in self.outputs:
            df[output] = getattr(self, output)
        return df.set_index('Time')
