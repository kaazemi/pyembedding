#!/usr/bin/env python

import os
import sys
SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
import random
import numpy
import subprocess
import tempfile
import shutil
import time
from collections import OrderedDict
from numba import jit

def make_partial_embedding(x, taus, preserve_indices=True):
    """Create a matrix of lagged vectors of x using lags in taus.
    
    If preserve_indices == True, then row i, column j of the matrix
    corresponds to x[i - taus[j]]; nonexistent entries are stored as nan.
    
    If preserve_indices == False, then row i, column j of the matrix
    corresponds to x[i + max(taus) - taus[j]; no nonexistent entries are present.
    """
    assert len(x.shape) == 1
    assert len(taus.shape) == 1
    
    N = x.shape[0]
    E = taus.shape[0]
    
    X = numpy.ones((N, E), dtype=float) * float('nan')
    for i, tau in enumerate(taus):
        assert tau >= 0
        X[tau:,i] = x[:(N-tau)]
    
    if not preserve_indices:
        X = X[numpy.max(taus):,:]
    
    return X
        

def make_full_embedding(x, E, tau=1, preserve_indices=True):
    """Create a matrix of lagged vectors of size E from vector x.
    
    The lagged vectors of length E form the rows of the matrix.
    
    In order to ensure that indices match conveniently, the shape of the matrix is
    (x.shape, E). Lagged vectors from time points outside of the time series are set
    to float('nan').
    
    >>> x = numpy.array([3.0, 1.7, 4.3, 5.4, 8.8, 9.6])
    >>> X = make_full_embedding(x, 3)
    >>> X.shape == (6,3)
    True
    >>> X[0,0] == 3.0
    True
    >>> numpy.isnan(X[0,1])
    True
    >>> numpy.isnan(X[0,2])
    True
    >>> X[1,0] == 1.7
    True
    >>> X[1,1] == 3.0
    True
    >>> numpy.isnan(X[1,2])
    True
    >>> numpy.sum(X[2,:] == [4.3, 1.7, 3.0])
    3
    >>> numpy.sum(X[3,:] == [5.4, 4.3, 1.7])
    3
    >>> numpy.sum(X[4,:] == [8.8, 5.4, 4.3])
    3
    >>> numpy.sum(X[5,:] == [9.6, 8.8, 5.4])
    3
    >>> X2 = make_full_embedding(x, 3, tau=2)
    >>> X2.shape[0]
    6
    >>> X2.shape[1]
    3
    >>> numpy.sum(X2[4,:] == [8.8, 4.3, 3.0])
    3
    >>> numpy.sum(X2[5,:] == [9.6, 5.4, 1.7])
    3
    """
    # Preconditions
    assert E > 0
    assert tau > 0
    assert len(x.shape) == 1
    
    return make_partial_embedding(x, numpy.arange(0, E*tau, tau), preserve_indices=preserve_indices)
    
#     N = x.shape[0]
#     
#     X = numpy.ones((N, E), dtype=float) * float('nan')
#     for i in range(E):
#         X[i*tau:,i] = x[:(N-i*tau)]
#     
#     return X

def squared_euclidean_distance(A, B):
    """Compute the squared Euclidean distance between all rows of A and all rows of B.
    
    >>> x = numpy.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    >>> X = make_full_embedding(x, 3)
    >>> D = squared_euclidean_distance(X, X)
    >>> numpy.array_equal(D[2:,2:], [[0, 3, 12, 27], [3, 0, 3, 12], [12, 3, 0, 3], [27, 12, 3, 0]])
    True
    """
    D = numpy.zeros((A.shape[0], B.shape[0]), dtype=float)
    for i in range(A.shape[0]):
        D[i,:] = ((B - A[i:(i+1),:].repeat(B.shape[0], axis=0))**2).sum(axis=1)
    return D

def euclidean_distance(A, B):
    return numpy.sqrt(squared_euclidean_distance(A, B))

@jit
def assign_diagonal(X, offset, val):
    """Initialize a value to all elements of a diagonal of X
    >>> x = numpy.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    >>> X = make_full_embedding(x, 3)
    >>> D = squared_euclidean_distance(X, X)
    >>> inf_val = float('inf')
    >>> assign_diagonal(D, 0, inf_val)
    >>> numpy.array_equal(D, [[inf_val, 3, 12, 27], [3, inf_val, 3, 12], [12, 3, inf_val, 3], [27, 12, 3, inf_val]])
    True
    >>> assign_diagonal(D, 1, inf_val)
    >>> numpy.array_equal(D, [[inf_val, inf_val, 12, 27], [3, inf_val, inf_val, 12], [12, 3, inf_val, inf_val], [27, 12, 3, inf_val]])
    True
    >>> assign_diagonal(D, -1, inf_val)
    >>> numpy.array_equal(D, [[inf_val, inf_val, 12, 27], [inf_val, inf_val, inf_val, 12], [12, inf_val, inf_val, inf_val], [27, 12, inf_val, inf_val]])
    True
    """
    for i in range(X.shape[0]):
        if (i + offset) >= 0 and (i + offset) < X.shape[1]:
            X[i, i + offset] = val
    
def find_neighbors(D, n_neighbors):
    """Find nearest neighbors from a distance matrix.
    >>> x = numpy.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    >>> X = make_full_embedding(x, 3)
    >>> D = squared_euclidean_distance(X, X)
    >>> assign_diagonal(D, 0, float('inf'))
    >>> N, DN = find_neighbors(D, 1)
    >>> numpy.array_equal(N[2:,:], [[3], [2], [3], [4]])
    True
    >>> assign_diagonal(D, 1, float('inf'))
    >>> assign_diagonal(D, -1, float('inf'))
    >>> N, DN = find_neighbors(D, 1)
    >>> numpy.array_equal(N[2:,:], [[4], [5], [2], [3]])
    True
    """
    N = numpy.zeros((D.shape[0], n_neighbors), dtype=int)
    DN = numpy.zeros((D.shape[0], n_neighbors), dtype=float)
    for i in range(D.shape[0]):
        N[i,:] = numpy.argpartition(D[:,i], range(n_neighbors))[:n_neighbors]
        DN[i,:] = D[i, N[i,:]]
    return N, DN

def nichkawde_embedding(x, E_max, neighbor_offset_min, preserve_indices=True):
    assert len(x.shape) == 1
    
    # Make a full embedding with maximum embedding dimension E_max;
    # actual embedding will consist of columns of this full embedding.
    X_full = make_full_embedding(x, E_max, tau=1, preserve_indices=preserve_indices)
    
    # Initial member of embedding is simply x[t] = x[t - 0]
    taus = (0,)
    while taus[-1] < (E_max - 1):
        print taus
        X = X_full[:,taus]
        D = numpy.sqrt(squared_euclidean_distance(X, X))
        
        # Don't want to use any exact matches
        D[D == 0.0] = float('inf')
        
        # Don't want to use any too close in time
        for offset in range(neighbor_offset_min):
            assign_diagonal(D, offset, float('nan'))
            if offset > 0:
                assign_diagonal(D, -offset, float('nan'))
        
        N, DN = find_neighbors(D, 1)
        
        max_deriv = 0.0
        max_deriv_tau = None
        for tau_next in range(taus[-1] + 1, E_max):
            Xnext = X_full[:,tau_next]
            Xnext_N = Xnext[N[:,0]]
            Dnext = numpy.abs(Xnext - Xnext_N)
            
            deriv = Dnext / DN[:,0]
            
            # Zero derivatives can arise from zero differences or infinite distances;
            # nan derivatives can arise from nan distances
            deriv = deriv[numpy.logical_and(numpy.logical_not(numpy.isnan(deriv)), (deriv != 0.0))]
            geo_mean_deriv = numpy.exp(numpy.log(deriv).mean())
            
            if geo_mean_deriv > max_deriv:
                max_deriv = geo_mean_deriv
                max_deriv_tau = tau_next
        
        if max_deriv_tau is None:
            raise Exception('Embedding identification failed: could not calculate max derivative.')
        taus = taus + (max_deriv_tau,)
    
    return taus, X_full[:,taus]

def configure(dir):
    cwd = os.getcwd()
    
    os.chdir(dir)
    proc = subprocess.Popen(['./configure'])
    result = proc.wait()
    if result != 0:
        raise Exception('configure returned nonzero status')
    
    os.chdir(cwd)

def make(dir):
    proc = subprocess.Popen(['make'], cwd=dir)
    result = proc.wait()
    if result != 0:
        raise Exception('make returned nonzero status')

def run_and_load_files(args, stdin_data, filenames):
    tmp_dir = tempfile.mkdtemp()
    
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        cwd=tmp_dir
    )
    stdout_data, stderr_data = proc.communicate(stdin_data)
    
    file_data_dict = OrderedDict()
    for filename in filenames:
        filepath = os.path.join(tmp_dir, filename)
        if os.path.exists(filepath):
            with open(filepath) as f:
                file_data = f.read()
            file_data_dict[filename] = file_data
    
    shutil.rmtree(tmp_dir)
    
    return stdout_data, stderr_data, file_data_dict

def uzal_parse_results(file_data):
    ms = []
    Lks = []
    for line in file_data.split('\n'):
        if not line.startswith('#') and len(line) > 0:
            try:
                pieces = line.split(' ')
                m = int(pieces[0]) + 1
                Lk = float(pieces[1])
                ms.append(m)
                Lks.append(Lk)
            except:
                pass
    
    return ms, Lks

def uzal_parse_params(stderr_data):
    params = {}
    for line in stderr_data.split('\n'):
        if line.startswith('Using T_M='):
            pieces = line.split('=')
            params['tw_max'] = int(pieces[1])
        elif line.startswith('Using ThW='):
            pieces = line.split('=')
            params['theiler_window'] = int(pieces[1])
        elif line.startswith('Using k='):
            pieces = line.split('=')
            params['n_neighbors'] = int(pieces[1].split(' ')[0])
    return params

def uzal_cost(x):
    """Runs the Uzal et al. cost function for full embeddings. Allows the costfunc program
    to identify the maximum embedding size, Theiler window, etc.
    """
    uzal_dir = os.path.join(SCRIPT_DIR, 'optimal_embedding')
    costfunc_path = os.path.join(uzal_dir, 'source_c', 'costfunc')
    
    if not os.path.exists(costfunc_path):
        configure(uzal_dir)
        make(uzal_dir)
    
    stdin_data = '\n'.join(['{0}'.format(xi) for xi in x])
    stdin_data += '\n'
    
    stdout_data, stderr_data, file_data_dict = run_and_load_files(
        [costfunc_path, '-e', '2'], stdin_data,
        ['stdin.amp']
    )
    
    ms, Lks = uzal_parse_results(file_data_dict['stdin.amp'])
    params = uzal_parse_params(stderr_data)
    
    return ms, Lks, params

if __name__ == '__main__':
    os.chdir(os.path.dirname(__file__))
    
    import doctest
    doctest.testmod(verbose=True)
    
    #x = numpy.random.normal(size=1000)
    #X = nichkawde_embedding(x, 5, 2)
