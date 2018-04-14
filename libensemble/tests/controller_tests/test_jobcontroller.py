#Test of job controller module for libensemble
#Test does not require running full libensemble
import os

def build_simfunc():
    import subprocess
    
    #Build simfunc
    #buildstring='mpif90 -o my_simjob.x my_simjob.f90' # On cray need to use ftn in place of mpif90
    buildstring='mpicc -o my_simjob.x my_simjob.c' # On cray need to use ftn in place of mpif90    
    os.chdir('simdir')
    #subprocess.run(buildstring.split(),check=True) #Python3.5+
    subprocess.check_call(buildstring.split())
    os.chdir('../')

#--------------- Calling script ---------------------------------------------------------------

from libensemble.register import Register, BalsamRegister
from libensemble.controller import JobController, BalsamJobController

sim_app = 'simdir/my_simjob.x'
#gen_app = 'gendir/my_genjob.x'

if not os.path.isfile(sim_app):
    build_simfunc()

USE_BALSAM = False

#Create and add exes to registry
if USE_BALSAM:
    registry = BalsamRegister()
    jobctrl = BalsamJobController(registry = registry)    
else:
    registry = Register()
    jobctrl = JobController(registry = registry)
    
registry.register_calc(full_path=sim_app, calc_type='sim')

#Alternative to IF could be using eg. fstring to specify: e.g:
#JOB_CONTROLLER = 'Balsam'
#registry = f"{JOB_CONTROLLER}Register()"

#--------------- Worker: sim func -------------------------------------------------------------
#Should work with Balsam or not


def polling_loop(job,timeout_sec=20.0,delay=2.0):
    import time
    start = time.time()
    
    while time.time() - start < timeout_sec:
        time.sleep(delay)
        print('Polling at time', time.time() - start)
        jobstate = job.poll()
        if job.finished: break
        elif jobstate == 'WAITING': print('Job waiting to launch')    
        elif jobstate == 'RUNNING': print('Job still running ....') 
        
        #Check output file for error
        #NOTE: currently balsam creates run in its own dir - so cant see file from here
        #      This should soon be fixed to have option to run in-place
        #      In mean time use the convience function for reading output files.
        
        #if 'Error' in open(outfilename).read(): #Non-Balsam only currently
        if 'Error' in job.read_file_in_workdir(outfilename): #Works if job controller creates different working dir.
            print("Found (deliberate) Error in ouput file - cancelling job")
            job.kill()
    
    if job.finished:
        if jobstate == 'FINISHED':
            print('Job finished succesfully. Status:',jobstate)
        elif jobstate == 'FAILED':
            print('Job failed. Status:',jobstate)  
        elif jobstate == 'USER_KILLED':
            print('Job has been killed. Status:',jobstate)
        else:
            print('Job status:', jobstate)
    else:
        print("Job timed out")
        job.kill()
        if job.finished: 
            print('Now killed')
            #double check
            jobstate = job.poll()
            print('Job state is', jobstate)
    
    
# Tests

#From worker call JobController by different name to ensure getting registered app from JobController
job = JobController.controller
#job = BalsamJobController.controller


print('\nTest 1 - should complete succesfully with status FINISHED :\n')
#machinefilename = 'machinefile_for_rank'
cores = 4
args_for_sim = 'sleep 5'
outfilename = 'out.txt'

job.launch(calc_type='sim', num_procs=cores, app_args=args_for_sim, stdout=outfilename)

#job.launch(calc_type='sim', machinefile=machinefilename, num_procs=cores, app_args=args_for_sim,
            #stdout=outfilename, test=True)
polling_loop(job)


print('\nTest 2 - Job should be USER_KILLED \n')
#machinefilename = 'machinefile_for_rank'
cores = 4
args_for_sim = 'sleep 5 Error'
outfilename = 'out.txt'

#From worker call JobController by different name to ensure getting registered app from JobController
job = JobController.controller
#job = BalsamJobController.controller

job.launch(calc_type='sim', num_procs=cores, app_args=args_for_sim, stdout=outfilename)

#job.launch(calc_type='sim', machinefile=machinefilename, num_procs=cores, app_args=args_for_sim,
            #stdout=outfilename, test=True)
polling_loop(job)








