"""
libEnsemble worker class
====================================================
"""

from __future__ import division
from __future__ import absolute_import

import socket
import logging
from itertools import count

import numpy as np

from libensemble.message_numbers import \
     EVAL_SIM_TAG, EVAL_GEN_TAG, \
     UNSET_TAG, STOP_TAG, CALC_EXCEPTION
from libensemble.message_numbers import \
     MAN_SIGNAL_FINISH, \
     MAN_SIGNAL_REQ_RESEND, MAN_SIGNAL_REQ_PICKLE_DUMP
from libensemble.message_numbers import calc_type_strings

from libensemble.loc_stack import LocationStack
from libensemble.calc_info import CalcInfo
from libensemble.controller import JobController

logger = logging.getLogger(__name__)
#For debug messages in this module  - uncomment
#  (see libE.py to change root logging level)
#logger.setLevel(logging.DEBUG)


def dump_pickle(pfilename, worker_out):
    """Write a pickle of the message."""
    import pickle
    with open(pfilename, "wb") as f:
        pickle.dump(worker_out, f)
    with open(pfilename, "rb") as f:
        pickle.load(f)  #check can read in this side
    return pfilename


def receive_and_run(comm, dtypes, worker, Work):
    """Receive data associated with a work order and run calc."""

    libE_info = Work['libE_info']
    calc_type = Work['tag']

    if len(libE_info['H_rows']) > 0:
        _, calc_in = comm.recv()
    else:
        calc_in = np.zeros(0, dtype=dtypes[calc_type])
    logger.debug("Received calc_in ({}) of len {}".format(calc_type_strings[calc_type], np.size(calc_in)))

    # comm will be in the future comms module...
    if libE_info.get('persistent'):
        libE_info['comm'] = comm
    calc_out, persis_info, calc_status = worker.run(Work, calc_in)
    libE_info.pop('comm', None)

    return {'calc_out': calc_out,
            'persis_info': persis_info,
            'libE_info': libE_info,
            'calc_status': calc_status,
            'calc_type': calc_type}


#The routine worker_main currently uses MPI.
#Comms will be implemented using comms module in future
def worker_main(comm, dtypes, sim_specs, gen_specs, workerID=None):
    """
    Evaluate calculations given to it by the manager.

    Creates a worker object, receives work from manager, runs worker,
    and communicates results. This routine also creates and writes to
    the workers summary file.

    Parameters
    ----------
    comm: comm object for manager communications

    dtypes: data types for sim/gen calculations

    sim_specs: dict with parameters/information for simulation calculations

    gen_specs: dict with parameters/information for generation calculations

    workerID: manager assigned worker ID (if None, default is comm.rank)
    """

    try:
        workerID = workerID or comm.rank
        worker = Worker(workerID, sim_specs, gen_specs)

        #Setup logging
        logger.info("Worker {} initiated on node {}". \
                    format(workerID, socket.gethostname()))

        # Print calc_list on-the-fly
        CalcInfo.create_worker_statfile(worker.workerID)

        #Init in case of manager request before filled
        worker_out = {'calc_status': UNSET_TAG}

        for worker_iter in count(start=1):
            logger.debug("Iteration {}".format(worker_iter))

            mtag, Work = comm.recv()
            if mtag == STOP_TAG:

                if Work == MAN_SIGNAL_FINISH: #shutdown the worker
                    break
                #Need to handle manager job kill here - as well as finish
                if Work == MAN_SIGNAL_REQ_RESEND:
                    logger.debug("Re-sending to Manager with status {}".\
                                 format(worker_out['calc_status']))
                    comm.send(0, worker_out)
                    continue

                if Work == MAN_SIGNAL_REQ_PICKLE_DUMP:
                    pfilename = "pickled_worker_{}_sim_{}.pkl".\
                      format(worker.workerID, worker.calc_iter[EVAL_SIM_TAG])
                    logger.debug("Make pickle for manager: status {}".\
                                 format(worker_out['calc_status']))
                    comm.send(0, dump_pickle(pfilename, worker_out))
                    continue

            worker_out = receive_and_run(comm, dtypes, worker, Work)

            # Check whether worker exited because it polled a manager signal
            if worker_out['calc_status'] == MAN_SIGNAL_FINISH:
                break

            logger.debug("Sending to Manager with status {}".\
                         format(worker_out['calc_status']))
            comm.send(0, worker_out)

    finally:
        comm.kill_pending()
        if sim_specs.get('clean_jobs'):
            worker.clean()


######################################################################
# Worker Class
######################################################################

class Worker:

    """The Worker Class provides methods for controlling sim and gen funcs"""

    # Worker Object methods
    def __init__(self, workerID, sim_specs, gen_specs):
        """Initialise new worker object.

        Parameters
        ----------

        workerID: int:
            The ID for this worker

        """
        self.workerID = workerID
        self.calc_iter = {EVAL_SIM_TAG : 0, EVAL_GEN_TAG : 0}
        self.loc_stack = Worker._make_sim_worker_dir(sim_specs, workerID)
        self._run_calc = Worker._make_runners(sim_specs, gen_specs)
        Worker._set_job_controller(workerID)


    @staticmethod
    def _make_sim_worker_dir(sim_specs, workerID, locs=None):
        "Create a dir for sim workers if 'sim_dir' is in sim_specs"
        locs = locs or LocationStack()
        if 'sim_dir' in sim_specs:
            sim_dir = sim_specs['sim_dir']
            prefix = sim_specs.get('sim_dir_prefix')
            worker_dir = "{}_{}".format(sim_dir, workerID)
            locs.register_loc(EVAL_SIM_TAG, worker_dir,
                              prefix=prefix, srcdir=sim_dir)
        return locs


    @staticmethod
    def _make_runners(sim_specs, gen_specs):
        "Create functions to run a sim or gen"

        sim_f = sim_specs['sim_f']
        gen_f = gen_specs['gen_f']

        def run_sim(calc_in, persis_info, libE_info):
            "Call the sim func."
            return sim_f(calc_in, persis_info, sim_specs, libE_info)

        def run_gen(calc_in, persis_info, libE_info):
            "Call the gen func."
            return gen_f(calc_in, persis_info, gen_specs, libE_info)

        return {EVAL_SIM_TAG: run_sim, EVAL_GEN_TAG: run_gen}


    @staticmethod
    def _set_job_controller(workerID):
        "Optional -- set worker ID in the job controller, return if set"
        try:
            jobctl = JobController.controller
            jobctl.set_workerID(workerID)
        except Exception:
            logger.info("No job_controller set on worker {}".\
                        format(workerID))
            return False
        else:
            return True


    def run(self, Work, calc_in):
        """Run a calculation on this worker object.

        This routine calls the user calculations. Exceptions are caught,
        dumped to the summary file, and raised.

        Parameters
        ----------

        Work: :obj:`dict`
            :ref:`(example)<datastruct-work-dict>`

        calc_in: obj: numpy structured array
            Rows from the :ref:`history array<datastruct-history-array>`
            for processing
        """
        calc_type = Work['tag']
        self.calc_iter[calc_type] += 1
        assert calc_type in [EVAL_SIM_TAG, EVAL_GEN_TAG], \
          "calc_type must either be EVAL_SIM_TAG or EVAL_GEN_TAG"

        # calc_stats stores timing and summary info for this Calc (sim or gen)
        calc_stats = CalcInfo()
        calc_stats.calc_type = calc_type

        try:
            calc = self._run_calc[calc_type]
            with calc_stats.timer:
                with self.loc_stack.loc(calc_type):
                    out = calc(calc_in, Work['persis_info'], Work['libE_info'])

            assert isinstance(out, tuple), \
              "Calculation output must be a tuple."
            assert len(out) >= 2, \
              "Calculation output must be at least two elements."

            calc_status = out[2] if len(out) >= 3 else UNSET_TAG
            return out[0], out[1], calc_status
        except Exception:
            calc_status = CALC_EXCEPTION
            raise
        finally:
            calc_stats.set_calc_status(calc_status)
            CalcInfo.add_calc_worker_statfile(calc=calc_stats)


    def clean(self):
        """Clean up calculation directories"""
        self.loc_stack.clean_locs()
