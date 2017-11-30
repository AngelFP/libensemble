"""
    sys.stdout.flush()
    import ipdb; ipdb.set_trace(context=21)
libEnsemble manager routines
====================================================
"""

from __future__ import division
from __future__ import absolute_import

# from message_numbers import EVAL_TAG # manager tells worker to evaluate the point 
from message_numbers import EVAL_SIM_TAG, FINISHED_PERSISTENT_SIM_TAG
from message_numbers import EVAL_GEN_TAG, FINISHED_PERSISTENT_GEN_TAG
from message_numbers import PERSIS_STOP
from message_numbers import STOP_TAG # manager tells worker run is over

from mpi4py import MPI
import numpy as np

import time, sys, os
import copy

def manager_main(comm, alloc_specs, sim_specs, gen_specs, failure_processing, exit_criteria, H0):

    H, H_ind, term_test, nonpersis_w, persis_w = initialize(sim_specs, gen_specs, alloc_specs, exit_criteria, H0)
    persistent_queue_data = {}; gen_info = {}

    send_initial_info_to_workers(comm, H, sim_specs, gen_specs, nonpersis_w)

    ### Continue receiving and giving until termination test is satisfied
    while not term_test(H, H_ind):

        H, H_ind, nonpersis_w, persis_w, gen_info = receive_from_sim_and_gen(comm, nonpersis_w, persis_w, H, H_ind, sim_specs, gen_specs, gen_info)

        persistent_queue_data = update_active_and_queue(H[:H_ind], gen_specs, persistent_queue_data)

        Work, gen_info = alloc_specs['alloc_f'](nonpersis_w, persis_w, H[:H_ind], sim_specs, gen_specs, gen_info)

        for w in Work:
            if term_test(H,H_ind):
                break
            nonpersis_w, persis_w = send_to_worker_and_update_active_and_idle(comm, H, Work[w], w, sim_specs, gen_specs, nonpersis_w, persis_w)

        # persis_w = give_information_to_persistent_workers(comm, persis_w)

    H, gen_info, exit_flag = final_receive_and_kill(comm, nonpersis_w, persis_w, H, H_ind, sim_specs, gen_specs, term_test, alloc_specs, gen_info)

    return H, gen_info, exit_flag





######################################################################
# Manager subroutines
######################################################################
# def give_information_to_persistent_workers(comm, persis_w):
#     # Tell all persistent workers to proceed

#     for i in persis_w['new_info']:
#         comm.send(obj=persis_w['new_info'][i], dest=i, tag=PERSIS_NEW_INFO)
#     persis_w['new_info'] = {}
        

#     for i in persis_w['stop']:
#         comm.send(obj=None, dest=i, tag=STOP_TAG)
#     persis_w['stop'] = set([])

#     return persis_w


def send_initial_info_to_workers(comm, H, sim_specs, gen_specs, nonpersis_w):
    # Communicate the gen dtype to workers to save time on future
    # communications. (Must communicate this when workers are requesting
    # libE_fields that aren't in sim_specs['out'] or gen_specs['out'])
    for w in nonpersis_w['waiting']:
        comm.send(obj=H[sim_specs['in']].dtype, dest=w)
        comm.send(obj=H[gen_specs['in']].dtype, dest=w)


def send_to_worker_and_update_active_and_idle(comm, H, Work, w, sim_specs, gen_specs, nonpersis_w, persis_w):

    comm.send(obj=Work['libE_info'], dest=w, tag=Work['tag'])
    comm.send(obj=Work['gen_info'], dest=w, tag=Work['tag'])
    if len(Work['libE_info']['H_rows']):
        comm.send(obj=H[Work['H_fields']][Work['libE_info']['H_rows']],dest=w)
    #     for i in Work['H_fields']:
    #         # comm.send(obj=H[i][0].dtype,dest=w)
    #         comm.Send(H[i][Work['libE_info']['H_rows']], dest=w)

    # Remove worker from either 'waiting' set and add it to the appropriate 'active' set
    nonpersis_w['waiting'].difference_update([w]); 
    persis_w['waiting'][Work['tag']].difference_update([w])
    if 'libE_info' in Work and 'persistent' in Work['libE_info']:
        persis_w[Work['tag']].add(w)
    else:
        nonpersis_w[Work['tag']].add(w)

    if 'blocking' in Work['libE_info']:
        nonpersis_w['blocked'].update(Work['libE_info']['blocking'])
        nonpersis_w['waiting'].difference_update(Work['libE_info']['blocking'])

    if Work['tag'] == EVAL_SIM_TAG:
        update_history_x_out(H, Work['libE_info']['H_rows'], w)

    return nonpersis_w, persis_w

def receive_from_sim_and_gen(comm, nonpersis_w, persis_w, H, H_ind, sim_specs, gen_specs, gen_info):
    status = MPI.Status()

    new_stuff = True
    while new_stuff and len(nonpersis_w[EVAL_SIM_TAG] | nonpersis_w[EVAL_GEN_TAG] | persis_w[EVAL_SIM_TAG] | persis_w[EVAL_GEN_TAG]) > 0:
        new_stuff = False
        for w in nonpersis_w[EVAL_SIM_TAG] | nonpersis_w[EVAL_GEN_TAG] | persis_w[EVAL_SIM_TAG] | persis_w[EVAL_GEN_TAG]: 
            if comm.Iprobe(source=w, tag=MPI.ANY_TAG, status=status):
                new_stuff = True

                D_recv = comm.recv(source=w, tag=MPI.ANY_TAG, status=status)
                recv_tag = status.Get_tag()
                assert recv_tag in [EVAL_SIM_TAG, EVAL_GEN_TAG, FINISHED_PERSISTENT_GEN_TAG], 'Unknown calculation tag received. Exiting'

                
                if recv_tag == EVAL_SIM_TAG:
                    update_history_f(H, D_recv)

                if recv_tag == EVAL_GEN_TAG:
                    H, H_ind = update_history_x_in(H, H_ind, w, D_recv['calc_out']) 

                if 'libE_info' in D_recv:
                    if 'blocking' in D_recv['libE_info']:
                        nonpersis_w['blocked'].difference_update(D_recv['libE_info']['blocking'])
                        nonpersis_w['waiting'].update(D_recv['libE_info']['blocking'])

                if 'gen_info' in D_recv:
                    for key in D_recv['gen_info'].keys():
                        gen_info[w][key] = D_recv['gen_info'][key]

                if recv_tag in [FINISHED_PERSISTENT_SIM_TAG, FINISHED_PERSISTENT_GEN_TAG]:
                    persis_w[EVAL_GEN_TAG].difference_update([w])
                    persis_w[EVAL_SIM_TAG].difference_update([w])
                    nonpersis_w['waiting'].add(w)

                else: 
                    if 'libE_info' in D_recv and 'persistent' in D_recv['libE_info']:
                        persis_w['waiting'][recv_tag].add(w)
                        persis_w[recv_tag].remove(w)
                    else:
                        nonpersis_w['waiting'].add(w)
                        nonpersis_w[recv_tag].remove(w)


    if 'save_every_k' in sim_specs:
        k = sim_specs['save_every_k']
        count = k*(sum(H['returned'])//k)
        filename = 'libE_history_after_sim_' + str(count) + '.npy'

        if not os.path.isfile(filename) and count > 0:
            np.save(filename,H)

    if 'save_every_k' in gen_specs:
        k = gen_specs['save_every_k']
        count = k*(H_ind//k)
        filename = 'libE_history_after_gen_' + str(count) + '.npy'

        if not os.path.isfile(filename) and count > 0:
            np.save(filename,H)

    return H, H_ind, nonpersis_w, persis_w, gen_info


def update_active_and_queue(H, gen_specs, data):
    """ Decide if active work should be continued and the queue order

    Parameters
    ----------
    H: numpy structured array
        History array storing rows for each point.
    """
    if 'queue_update_function' in gen_specs and len(H):
        H, data = gen_specs['queue_update_function'](H,gen_specs, data)
    
    return data


def update_history_f(H, D): 
    """
    Updates the history (in place) after a point has been evaluated

    Parameters
    ----------
    H: numpy structured array
        History array storing rows for each point.
    """

    new_inds = D['libE_info']['H_rows']
    H_0 = D['calc_out']

    for j,ind in enumerate(new_inds): 
        for field in H_0.dtype.names:
            H[field][ind] = H_0[field][j]

        H['returned'][ind] = True


def update_history_x_out(H, q_inds, sim_rank):
    """
    Updates the history (in place) when a new point has been given out to be evaluated

    Parameters
    ----------
    H: numpy structured array
        History array storing rows for each point.
    H_ind: integer
        The new point
    W: numpy array
        Work to be evaluated
    lead_rank: int
        lead ranks for the evaluation of x 
    """

    for i,j in zip(q_inds,range(len(q_inds))):
        H['given'][i] = True
        H['given_time'][i] = time.time()
        H['sim_rank'][i] = sim_rank


def update_history_x_in(H, H_ind, gen_rank, O):
    """
    Updates the history (in place) when a new point has been returned from a gen

    Parameters
    ----------
    H: numpy structured array
        History array storing rows for each point.
    H_ind: integer
        The new point
    O: numpy array
        Output from gen_f
    """

    if len(O) == 0:
        return H, H_ind

    rows_remaining = len(H)-H_ind
    
    if 'sim_id' not in O.dtype.names:
        # gen method must not be adjusting sim_id, just append to H
        num_new = len(O)

        if num_new > rows_remaining:
            H = grow_H(H,num_new-rows_remaining)
            
        update_inds = np.arange(H_ind,H_ind+num_new)
        H['sim_id'][H_ind:H_ind+num_new] = range(H_ind,H_ind+num_new)
    else:
        # gen method is building sim_id. 
        num_new = len(np.setdiff1d(O['sim_id'],H['sim_id']))

        if num_new > rows_remaining:
            H = grow_H(H,num_new-rows_remaining)

        update_inds = O['sim_id']
        
    for field in O.dtype.names:
        H[field][update_inds] = O[field]

    H['gen_rank'][update_inds] = gen_rank

    H_ind += num_new

    return H, H_ind


def grow_H(H, k):
    """ libEnsemble is requesting k rows be added to H because gen_f produced
    more points than rows in H."""
    H_1 = np.zeros(k, dtype=H.dtype)
    H_1['sim_id'] = -1
    H_1['given_time'] = np.inf

    H = np.append(H,H_1)

    return H



def termination_test(H, H_ind, exit_criteria, start_time, lenH0):
    """
    Return nonzero if the libEnsemble run should stop 
    """

    if 'sim_max' in exit_criteria:
        if np.sum(H['given']) >= exit_criteria['sim_max'] + lenH0:
            return 1

    if 'gen_max' in exit_criteria:
        if H_ind >= exit_criteria['gen_max'] + lenH0:
            return 1 

    if 'stop_val' in exit_criteria:
        key = exit_criteria['stop_val'][0]
        val = exit_criteria['stop_val'][1]
        if np.any(H[key][:H_ind][~np.isnan(H[key][:H_ind])] <= val): 
            return 1

    if 'elapsed_wallclock_time' in exit_criteria:
        if time.time() - start_time >= exit_criteria['elapsed_wallclock_time']:
            return 2

    return False


def initialize(sim_specs, gen_specs, alloc_specs, exit_criteria, H0):
    """
    Forms the numpy structured array that records everything from the
    libEnsemble run 

    Returns
    ----------
    H: numpy structured array
        History array storing rows for each point. Field names are below

        | sim_id              : Identifier for each simulation (could be the same "point" just with different parameters) 
        | given               : True if point has been given to a worker
        | given_time          : Time point was given to a worker
        | sim_rank            : worker rank that evaluated point
        | gen_rank            : worker rank that generated point 
        | returned            : True if point has been evaluated by a worker

    H_ind: integer
        Where libEnsemble should start filling in H

    term_test: lambda funciton
        Simplified termination test (doesn't require passing fixed quantities).
        This is nice when calling term_test in multiple places.
    """

    if 'sim_max' in exit_criteria:
        L = exit_criteria['sim_max']
    else:
        L = 100

    from libE_fields import libE_fields

    H = np.zeros(L + len(H0), dtype=list(set(libE_fields + sim_specs['out'] + gen_specs['out'] + alloc_specs['out'])))

    if len(H0):
        fields = H0.dtype.names

        for field in fields:
            H[field][:len(H0)] = H0[field]
            # for ind, val in np.ndenumerate(H0[field]): # Works if H0[field] has arbitrary dimension but is slow
            #     H[field][ind] = val

    # Prepend H with H0 
    H['sim_id'][:len(H0)] = np.arange(0,len(H0))
    H['given'][:len(H0)] = 1
    H['returned'][:len(H0)] = 1

    H['sim_id'][-L:] = -1
    H['given_time'][-L:] = np.inf

    H_ind = len(H0)
    start_time = time.time()
    term_test = lambda H, H_ind: termination_test(H, H_ind, exit_criteria, start_time, len(H0))

    nonpersis_w = {'waiting': alloc_specs['worker_ranks'].copy(), EVAL_GEN_TAG:set(), EVAL_SIM_TAG:set(), 'blocked':set()}
    persis_w = {'waiting':{EVAL_SIM_TAG:set(),EVAL_GEN_TAG:set()}, EVAL_SIM_TAG:set(), EVAL_GEN_TAG:set()}

    return H, H_ind, term_test, nonpersis_w, persis_w

def final_receive_and_kill(comm, nonpersis_w, persis_w, H, H_ind, sim_specs, gen_specs, term_test, alloc_specs, gen_info):
    """ 
    Tries to receive from any active workers. 

    If time expires before all active workers have been received from, a
    nonblocking receive is posted (though the manager will not receive this
    data) and a kill signal is sent. 
    """

    exit_flag = 0

    ### Receive from all active workers 
    while len(nonpersis_w[EVAL_SIM_TAG] | nonpersis_w[EVAL_GEN_TAG] | persis_w[EVAL_SIM_TAG] | persis_w[EVAL_GEN_TAG]):
        H, H_ind, nonpersis_w, persis_w, gen_info = receive_from_sim_and_gen(comm, nonpersis_w, persis_w, H, H_ind, sim_specs, gen_specs, gen_info)
        if term_test(H, H_ind) == 2 and len(nonpersis_w[EVAL_SIM_TAG] | nonpersis_w[EVAL_GEN_TAG] | persis_w[EVAL_SIM_TAG] | persis_w[EVAL_GEN_TAG]):
            print("Termination due to elapsed_wallclock_time has occurred.\n"\
              "A last attempt has been made to receive any completed work.\n"\
              "Posting nonblocking receives and kill messages for all active workers\n")

            for w in nonpersis_w[EVAL_SIM_TAG] | nonpersis_w[EVAL_GEN_TAG] | persis_w[EVAL_SIM_TAG] | persis_w[EVAL_GEN_TAG]:
                comm.irecv(source=w, tag=MPI.ANY_TAG)
            exit_flag = 2
            break

    ### Kill the workers
    for w in alloc_specs['worker_ranks']:
        comm.send(obj=None, dest=w, tag=STOP_TAG)

    return H[:H_ind], gen_info, exit_flag
