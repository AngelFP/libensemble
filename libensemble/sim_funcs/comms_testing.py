from __future__ import division
from __future__ import print_function

import numpy as np
from libensemble.controller import JobController


def float_x1000(H, gen_info, sim_specs, libE_info):
    """
    Multiplies worker ID by 1000 and sends back values
    Input (X) is ignored in this case
    """
    
    #All values
    output = np.zeros(1,dtype=sim_specs['out'])
    
    #First test fill - even though - it will do arr_vals and scal_val
    jobctl = JobController.controller 
    x = jobctl.workerID * 1000.0
    
    output.fill(x) #All set
    output['scal_val'] = x + x/1e7 #Make scalar value distinct

    return output, gen_info


#def input_double(H, gen_info, sim_specs, libE_info):
    #"""
    #Not yet implemented
    #Multiplies input X values by 2 and sends back values

    #"""
    #pass