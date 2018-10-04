# """
# Runs libEnsemble on the 6-hump camel problem. Documented here:
#    https://www.sfu.ca/~ssurjano/camel6.html
#
# Execute via the following command:
#    mpiexec -np 4 python3 call_6-hump_camel.py
# The number of concurrent evaluations of the objective function will be 4-1=3.
# """

from __future__ import division
from __future__ import absolute_import

import sys, os             # for adding to path
import numpy as np

if len(sys.argv) > 1 and sys.argv[1] == "--threads":
    from libensemble.libE_thread import libE
    nworkers = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    is_master = True
    libE_specs = {'nworkers': nworkers}
elif len(sys.argv) > 1 and sys.argv[1] == "--processes":
    from libensemble.libE_process import libE
    nworkers = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    is_master = True
    libE_specs = {'nworkers': nworkers}
else:
    from mpi4py import MPI #
    from libensemble.libE import libE
    nworkers = MPI.COMM_WORLD.Get_size()-1
    is_master = MPI.COMM_WORLD.Get_rank() == 0
    libE_specs = {'comm': MPI.COMM_WORLD, 'color': 0}

# Import sim_func
from libensemble.sim_funcs.six_hump_camel import six_hump_camel_simple

# Import gen_func
from libensemble.gen_funcs.uniform_sampling import uniform_random_sample

# Import alloc_func
from libensemble.alloc_funcs.fast_alloc import give_sim_work_first as alloc_f

script_name = os.path.splitext(os.path.basename(__file__))[0]

for time in np.append([0], np.logspace(-5,-1,5)):
    for rep in range(1):
        #State the objective function, its arguments, output, and necessary parameters (and their sizes)
        sim_specs = {'sim_f': six_hump_camel_simple, # This is the function whose output is being minimized
                     'in': ['x'], # These keys will be given to the above function
                     'out': [('f',float), # This is the output from the function being minimized
                            ],
                     'pause_time':time,
                     }
        if time == 0:
            sim_specs.pop('pause_time')

        # State the generating function, its arguments, output, and necessary parameters.
        num_pts = 30*nworkers
        gen_specs = {'gen_f': uniform_random_sample,
                     'in': [],
                     'out': [('x',float,2),
                            ],
                     'lb': np.array([-3,-2]),
                     'ub': np.array([ 3, 2]),
                     'gen_batch_size': num_pts,
                     'num_active_gens':1,
                     }

        if time == 0:
            gen_specs['gen_batch_size'] = num_pts//2

        # Tell libEnsemble when to stop
        exit_criteria = {'sim_max': num_pts}

        np.random.seed(1)
        persis_info = {'next_to_give':0}
        persis_info['total_gen_calls'] = 1
        for i in range(1,nworkers+1):
            persis_info[i] = {'rand_stream': np.random.RandomState(i)}

        alloc_specs = {'out':[('allocated',bool)], 'alloc_f':alloc_f}
        # Perform the run

        H, persis_info, flag = libE(sim_specs, gen_specs, exit_criteria, persis_info, alloc_specs, libE_specs)

        if is_master:
            assert len(H) == num_pts
