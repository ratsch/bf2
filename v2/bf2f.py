#bin/python
# Skeleton!

import numpy as np
from copy import deepcopy
import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
import sys
import gzip
import time
#import pathos.multiprocessing as mp

# --- CONSTANTS --- #
EXACT=True
NOISE=False
PERSISTENT=True
THEANO=False
VERBOSE=True
if NOISE: EXACT=False
if EXACT or NOISE: PERSISTENT=False
if THEANO:
    from theano import function, shared, scan
    import theano.tensor as tten
print 'EXACT:', str(EXACT)
print 'PERSISTENT:', str(PERSISTENT)
print 'NOISE:', str(NOISE)
print 'THEANO:', str(THEANO)
# yolo
#linn = mp.ProcessingPool(5)

class data_stream(object):
    """
    Class for data stream.
    (can use this as a generator)
    """
    def __init__(self, path):
        self.path = path
    def __iter__(self):
        """
        Just spits out lines from the file.
        """
        fi = gzip.open(self.path,'r')
        header = fi.readline()
        while True:
            line = fi.readline()
            if len(line) == 0: # EOF
                break
            else:
                example = map(int, line.split())
                yield example
    def get_vocab_sizes(self):
        """
        The first line of the data file should contain W, R.
        """
        fi = gzip.open(self.path,'r')
        header = fi.readline()
        fi.close()
        values = map(int, header.split())
        if len(values) == 2:
            W, R = values
        else:
            sys.exit('ERROR: data file incorrectly formatted.')
        return W, R
    def acquire_all(self):
        """
        Just suck it all in!
        """
        traindata = [[0, 0, 0]]
        fi = gzip.open(self.path, 'r')
        header = fi.readline()
        for line in fi:
            s, r, t = map(int, line.split())
            traindata.append([s, r, t])
        return np.array(traindata[1:])

class theano_params(object):
    """
    Parameter object which is... theano-ey.
    """
    def __init__(self, initial_parameters):
        C, G, V = initial_parameters
        if C.shape != V.shape:
            raise ValueError
        if G.shape[1] != C.shape[1]:
            raise ValueError
        if G.shape[2] != C.shape[1]:
            raise ValueError
        self.W = C.shape[0]
        self.R = G.shape[0]
        self.d = C.shape[1] - 1
        # --- initialise shared variables --- #
        # weights
        self.C = shared(np.float32(C), 'C')
        self.G = shared(np.float32(G), 'G')
        self.V = shared(np.float32(V), 'V')
        # velocities
        self.C_vel = shared(np.zeros(shape=C.shape, dtype=np.float32), 'C_vel')
        self.G_vel = shared(np.zeros(shape=G.shape, dtype=np.float32), 'G_vel')
        self.V_vel = shared(np.zeros(shape=V.shape, dtype=np.float32), 'V_vel')
        # --- define theano functions --- #
        # symbolic variables
        C_locs = tten.ivector('C_locs')
        G_locs = tten.ivector('G_locs')
        V_locs = tten.ivector('V_locs')
        GC = tten.fmatrix('GC')
        muC = tten.fscalar('muC')
        muG = tten.fscalar('muG')
        muV = tten.fscalar('muV')
        alphaC = tten.fscalar('alphaC')
        alphaG = tten.fscalar('alphaG')
        alphaV = tten.fscalar('alphaV')
        deltaC = tten.fmatrix('deltaC')
        deltaG = tten.ftensor3('deltaG')
        deltaV = tten.fmatrix('deltaV')
        # energies
        self.GC  = function([C_locs, G_locs],
                            tten.batched_dot(self.G[G_locs], self.C[C_locs]))
        self.energies = function([GC, V_locs],
                                 -tten.sum(self.V[V_locs]*GC, axis=1))
        # gradients
        self.VG = function([G_locs, V_locs],
                           tten.batched_dot(self.V[V_locs], self.G[G_locs]))
        VeeCee, updates = scan(fn=lambda C_loc, V_loc: -tten.outer(self.V[V_loc], self.C[C_loc]), outputs_info=None, sequences=[C_locs, V_locs])
        self.VC = function([C_locs, V_locs], VeeCee)

        # updates
        velocity_inputs = [deltaC, deltaG, deltaV, muC, muG, muV]
        velocity_updates = [(self.C_vel, muC*self.C_vel + (1 - muC)*deltaC),
                            (self.G_vel, muG*self.G_vel + (1 - muG)*deltaG),
                            (self.V_vel, muV*self.V_vel + (1 - muV)*deltaV)]
        parameter_updates = [(self.C, self.C + alphaC*self.C_vel),
                             (self.G, self.G + alphaG*self.G_vel),
                             (self.V, self.V + alphaV*self.V_vel)]
        parameter_inputs = [alphaC, alphaG, alphaV]
        self.update_velocities = function(velocity_inputs, [],
                                          updates=velocity_updates,
                                          allow_input_downcast=True)
        self.update_parameters = function(parameter_inputs, [],
                                          updates=parameter_updates,
                                          allow_input_downcast=True)
        
    def update(self, delta_parameters, alpha, mu):
        """
        Update velocities and then parameters.
        """
        # unwrap
        deltaC, deltaG, deltaV = delta_parameters
        alphaC, alphaG, alphaV = alpha
        muC, muG, muV = mu
        # call theano fns
        self.update_velocities(deltaC, deltaG, deltaV, muC, muG, muV)
        self.update_parameters(alphaC, alphaG, alphaV)

    def grad_E(self, locations):
        """
        Gradients of the energy, evaluated at a list of triples.
        NOTE: this clearly depends on the choice of energy.
        Returns tensors whose first index corresponds to the input triple list.
        """
        C_locs = list(locations[:, 0])
        G_locs = list(locations[:, 1])
        V_locs = list(locations[:, 2])
        # call theano functions
        dE_C = -self.VG(G_locs, V_locs)
        dE_G = self.VC(C_locs, V_locs)
        dE_V = -self.GC(C_locs, G_locs)
        return dE_C, dE_G, dE_V

    def E(self, locations):
        """
        Just plain old energy between triples.
        locations is an array of triples.
        Outputs a list (same length as 'locations') of energy of each triple.
        """
        C_locs = list(locations[:, 0])
        G_locs = list(locations[:, 1])
        V_locs = list(locations[:, 2])
        # call theano functions
        GC = self.GC(C_locs, G_locs)
        energy = self.energies(GC, V_locs)
        return energy

    def sample(self, seed, K):
        """
        Draws samples from the model, given a (single!) seed.
        (iterates through Gibbs sampling K times)
        """
        W = self.W
        R = self.R
        ss = deepcopy(seed)
        for iteration in xrange(K):
            order = np.random.permutation(3)
            for triple_drop in order:
                if triple_drop == 0:
                    locs = np.array([ [i, ss[1], ss[2]] for i in xrange(W) ])
                if triple_drop == 1:
                    locs = np.array([ [ss[0], i, ss[2]] for i in xrange(R) ])
                if triple_drop == 2:
                    locs = np.array([ [ss[0], ss[1], i] for i in xrange(W) ])
                expmE = np.exp(-self.E(locs))
                probs = expmE/np.sum(expmE)
                samp = np.random.choice(len(probs), p=probs, size=1)[0]
                ss[triple_drop] = samp
        return ss

    def get_parameters(self):
        """
        Method to return the (C, G, V) triple.
        """
        return (self.C.get_value(), self.G.get_value(), self.V.get_value())

def silly_energy(arg):
    C, G, V = arg[0]
    triple = arg[1]
    s, r, t = triple
    energy = -np.dot(V[t], np.dot(G[r], C[s]))
    return energy

class params(object):
    """
    Parameter object.
    Contains C, G, V and velocities for all.
    """
    def __init__(self, initial_parameters):
        C, G, V = initial_parameters
        if C.shape != V.shape:
            raise ValueError
        if G.shape[1] != C.shape[1]:
            raise ValueError
        if G.shape[2] != C.shape[1]:
            raise ValueError
        self.W = C.shape[0]
        self.R = G.shape[0]
        self.d = C.shape[1] - 1
        # weights
        self.C = C
        self.G = G
        self.V = V
        # velocities
        self.C_vel = np.zeros(shape=self.C.shape)
        self.G_vel = np.zeros(shape=self.G.shape)
        self.V_vel = np.zeros(shape=self.V.shape)

    def update(self, delta_parameters, alpha, mu):
        """
        Updates velocities and then parameters.
        """
        # unwrap
        deltaC, deltaG, deltaV = delta_parameters
        alphaC, alphaG, alphaV = alpha
        muC, muG, muV = mu
        # update velocities
        self.C_vel = muC*self.C_vel + (1-muC)*deltaC
        self.G_vel = muG*self.G_vel + (1-muG)*deltaG
        self.V_vel = muV*self.V_vel + (1-muV)*deltaV
        # update parameters
        self.C += alphaC*self.C_vel
        self.G += alphaG*self.G_vel
        self.V += alphaV*self.V_vel

    def grad_E(self, locations):
        """
        Gradients of the energy, evaluated at a list of triples.
        NOTE: this clearly depends on the choice of energy.
        Returns tensors whose first index corresponds to the input triple list.
        """
        C_sub = self.C[locations[:, 0]]
        G_sub = self.G[locations[:, 1]]
        V_sub = self.V[locations[:, 2]]
        # this is for Etype == 'dot'
        # TODO: make this efficient
        dE_C = -np.einsum('...i,...ij', V_sub, G_sub)
        dE_G = -np.einsum('...i,...j', V_sub, C_sub)
        dE_V = -np.einsum('...ij,...j', G_sub, C_sub)
        return dE_C, dE_G, dE_V

    def E_axis(self, triple, switch):
        """
        Returns energies over an axis (S, R, T) given two of the triple.
        """
        s, r, t = triple
        if switch == 'C':
            # return over all S
            #GC = np.dot(self.C, self.G[r].T)
            #energy = -np.dot(GC, self.V[t])
            # note: above version is significantly slower than the below
            VG = np.dot(self.V[t], self.G[r])
            energy = -np.dot(self.C, VG)
        elif switch == 'G':
            # return over all R
            VG = np.dot(self.V[t], self.G)
            energy = -np.dot(VG, self.C[s])
        elif switch == 'V':
            #return over all T
            GC = np.dot(self.G[r], self.C[s])
            energy = -np.dot(self.V, GC)
        else:
            print 'ERROR: Cannot parse switch.'
            sys.exit()
        return energy

    def E_triple(self, triple):
        """
        The energy of a SINGLE triple.
        """
        return -np.dot(self.V[triple[2]], np.dot(self.G[triple[1]], self.C[triple[0]]))

    def E(self, locations):
        """
        Just plain old energy between triples.
        locations is an array of triples.
        Outputs a list (same length as 'locations') of energy of each triple.
        """
        #C_sub = self.C[locations[:, 0]]
        #G_sub = self.G[locations[:, 1]]
        #V_sub = self.V[locations[:, 2]]
        # this is for Etype == 'dot'
        # TODO: 
        #   profile speed wrt order
        #   wrt just looping through locations
        #   # yolo
        # profiling...
        # V1
        #GC_sub = np.einsum('...ij,...j', G_sub, C_sub)
        #energy = -np.einsum('...i,...i', V_sub, GC_sub)
        # V2
        #energy = np.empty(shape=(len(locations)))
        #for i in xrange(len(locations)):
        #    energy[i] = -np.dot(C_sub[i],np.dot(V_sub[i], G_sub[i]))
        # V3
        #VG_sub = np.einsum('...i,...ij', V_sub, G_sub)
        #energy = -np.einsum('...i,...i', VG_sub, C_sub)
        # V4
        #energy = map(lambda triple: -np.dot(self.C[triple[0]], np.dot(self.V[triple[2]], self.G[triple[1]])), locations)
        #energy = np.array(map(lambda (s, r, t): -np.dot(self.C[s], np.dot(self.V[t], self.G[r])), locations))
        # V5
        #energy = map(lambda i: -np.dot(V_sub[i], np.dot(G_sub[i], C_sub[i])), xrange(len(locations)))
        # V6
        #energy = linn.amap(self.E_triple, locations)
        # V7
        #parmz = []
        #for triple in locations:
        #    parmz.append(((self.C, self.G, self.V), triple))
        #energy = map(silly_energy, parmz)
        # V8
        #energy = np.empty(shape=len(locations))
        #for (i, triple) in enumerate(locations):
        #    energy[i] = self.E_triple(triple)
        # V9
        energy = np.empty(shape=len(locations), dtype=np.float)
        for (i, triple) in enumerate(locations):
            energy[i] = -np.dot(self.C[triple[0]], np.dot(self.V[triple[2]], self.G[triple[1]]))
        # V10
        #energy = []
        #for triple in locations:
        #    energy.append(-np.dot(self.C[triple[0]], np.dot(self.V[triple[2]], self.G[triple[1]])))
        return energy
  
    def sample(self, seed, K):
        """
        Draws samples from the model, given a (single!) seed.
        (iterates through Gibbs sampling K times)
        """
        W = self.W
        R = self.R
        ss = deepcopy(seed)
        for iteration in xrange(K):
            order = np.random.permutation(3)
            for triple_drop in order:
                if triple_drop == 0:
                    energy = self.E_axis(ss, 'C')
                    #locs = np.array([ [i, ss[1], ss[2]] for i in xrange(W) ])
                if triple_drop == 1:
                    energy = self.E_axis(ss, 'G')
                    #locs = np.array([ [ss[0], i, ss[2]] for i in xrange(R) ])
                if triple_drop == 2:
                    energy = self.E_axis(ss, 'V')
                    #locs = np.array([ [ss[0], ss[1], i] for i in xrange(W) ])
                #expmE = np.exp(-self.E(locs))
                expmE = np.exp(-energy)
                probs = expmE/np.sum(expmE)
                samp = np.random.choice(len(probs), p=probs, size=1)[0]
                ss[triple_drop] = samp
        return ss

    def get_parameters(self):
        """
        Method to return the (C, G, V) triple.
        """
        return (self.C, self.G, self.V)

def log_likelihood(parameters, data):
    """
    WARNING: Probably don't want to do this most of the time.
    Requires 'data' to be a full list (not just a generator, I think...)
    """
    W = parameters.W
    R = parameters.R
    locations = np.array([[s, r, t] for s in xrange(W) for r in xrange(R) for t in xrange(W) ])
    energy = parameters.E(locations).reshape(W, R, W)
    logZ = np.log(np.sum(np.exp(-energy)))
    ll = np.sum([(-energy[s, r, t] - logZ) for s, r, t in data])
    return ll

def sample_noise(W, R, M):
    """
    Return M totally random samples.
    TODO: allow for other noise distribution.
    """
    noise_samples = np.array(zip(np.random.randint(0, W, M),
                                 np.random.randint(0, R, M),
                                 np.random.randint(0, W, M)))
    return noise_samples

def Z_gradient(parameters):
    """
    Calculates EXACT gradient of the partition function.
    NOTE: intractable most of the time.
    This should possibly belong to the parameters.
    """
    W = parameters.W
    R = parameters.R
    d = parameters.d
    locations = np.array([[s, r, t] for s in xrange(W) for r in xrange(R) for t in xrange(W) ])
    # get exponentiated energy
    energy = parameters.E(locations).reshape(W, R, W)
    expmE = np.exp(-energy)
    Z = np.sum(expmE)
    # get gradients
    dE_C, dE_G, dE_V = parameters.grad_E(locations)
    # empty arrays
    dC_partition = np.zeros(shape=(W, d+1))
    dG_partition = np.zeros(shape=(R, d+1, d+1))
    dV_partition = np.zeros(shape=(W, d+1))
    for (n, (s, r, t)) in enumerate(locations):
        dC_partition[s, :] -= dE_C[n, :]*expmE[s, r, t]
        dG_partition[r, :, :] -= dE_G[n, :, :]*expmE[s, r, t]
        dV_partition[t, :] -= dE_V[n, :]*expmE[s, r, t]
    dC_partition /= Z
    dG_partition /= Z
    dV_partition /= Z
    return dC_partition, dG_partition, dV_partition

def batch_gradient(parameters, batch):
    """
    Gradient is a difference of contributions from:
    1. data distribution (batch of training examples)
    2. model distribution (batch of model samples)
    In both cases, we need to evaluate a gradient over a batch of triples.
    This is a general function for both tasks
    (so we expect to call it twice for each 'true' gradient evaluation.)
    """
    W = parameters.W
    R = parameters.R
    d = parameters.d
    dC_batch = np.zeros(shape=(W, d+1))
    dG_batch = np.zeros(shape=(R, d+1, d+1))
    dV_batch = np.zeros(shape=(W, d+1))
    dE_C_batch, dE_G_batch, dE_V_batch = parameters.grad_E(batch)
    for (i, (s, r, t)) in enumerate(batch):
        dC_batch[s, :] -= dE_C_batch[i]
        dG_batch[r, :, :] -= dE_G_batch[i]
        dV_batch[t, :] -= dE_V_batch[i]
    return (dC_batch, dG_batch, dV_batch)

def combine_gradients(delta_data, delta_model, B, M):
    """
    Just combines two triples...
    """
    # TODO: make this logic more clear/move it elsewhere
    if EXACT:
        prefactor = float(B)
    else:
        prefactor = float(B)/M
    delta_C = delta_data[0] - prefactor*delta_model[0]
    delta_G = delta_data[1] - prefactor*delta_model[1]
    delta_V = delta_data[2] - prefactor*delta_model[2]
    # impose constraints
    delta_C[:, -1] = 0
    delta_V[:, -1] = 0
    #delta_G[:, -1, :] = 0
    # yolo
    delta_G[:, :, :] = 0
    return delta_C, delta_G, delta_V

def train(training_data, start_parameters, options):
    """
    Perform (stochastic) gradient ascent on the parameters.
    INPUTS:
        training_data       iterator of examples.
        start_parameters    triple of (C, G, V)
        options             dictionary
    RETURNS:
        parameters      triple of (C, G, V)
        [[ some measure of convergence ]]
    """
    # unwrap options
    B = options['batch_size']
    S = options['sampling_rate']
    M = options['num_samples']
    D = options['diagnostics_rate']
    K = options['gibbs_iterations']
    calculate_ll = options['calculate_ll']
    alpha, mu = options['alpha'], options['mu']
    logfile = options['logfile']
    # initialise
    vali_set = set()
    batch = np.empty(shape=(B, 3),dtype=np.int)
    # TODO: proper sample initialisation
    samples = np.zeros(shape=(M, 3),dtype=np.int)
    if THEANO:
        parameters = theano_params(start_parameters)
    else:
        parameters = params(start_parameters)
    # diagnostic things
    logf = open(logfile,'w')
    logf.write('n\tt\tll\tde\tme\tve\tre\n')
    W = parameters.W
    R = parameters.R
    n = 0
    t0 = time.time()
    for example in training_data:
        if len(vali_set) < D:
            vali_set.add(tuple(example))
            continue
        # yolo...
        if not W == 5:
            if tuple(example) in vali_set:
                continue
        batch[n%B, :] = example
        n += 1
        if not EXACT and n%S == 0:
            if NOISE:
                samples = sample_noise(W, R, S)
            else:
                if not PERSISTENT: samples[:, :] = batch[:, :]
                for (m, samp) in enumerate(samples):
                    samples[m, :] = parameters.sample(samp, K)
            delta_model = batch_gradient(parameters, samples)
        if n%B == 0:
            if EXACT:
                delta_model = Z_gradient(parameters)
            delta_data = batch_gradient(parameters, batch)
            delta_params = combine_gradients(delta_data, delta_model, B, len(samples))
            parameters.update(delta_params, alpha, mu)
        if n%D == 0 and n > B:
            t = time.time() - t0
            if calculate_ll:
                ll = log_likelihood(parameters, training_data)
            else:
                ll = 'NA'
            data_energy = np.mean(parameters.E(batch))
            vali_energy = np.mean(parameters.E(np.array(list(vali_set))))
            random_lox = np.array(zip(np.random.randint(0, W, 100),
                                      np.random.randint(0, R, 100),
                                      np.random.randint(0, W, 100)))
            rand_energy = np.mean(parameters.E(random_lox))
            if PERSISTENT:
                model_energy = np.mean(parameters.E(samples))
            else:
                model_energy = 'NA'
            logline = [n, t, ll, data_energy, model_energy, vali_energy, rand_energy]
            if VERBOSE:
                for val in logline:
                    if type(val) == float:
                        print '\t','%.3f' % val,
                    else:
                        print '\t', val,
                print ''
            logf.write('\t'.join(map(str, logline))+'\n')
            logf.flush()
    print 'Training done,', n, 'examples seen.'
    return parameters
