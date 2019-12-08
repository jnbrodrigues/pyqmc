import numpy as np
import pandas as pd
import scipy
import h5py
import pyqmc
import pyqmc.hdftools as hdftools


def ortho_hdf(hdf_file, data, attr, configs):

    if hdf_file is not None:
        with h5py.File(hdf_file, "a") as hdf:
            if "configs" not in hdf.keys():
                hdftools.setup_hdf(hdf, data, attr)
                hdf.create_dataset("configs", configs.configs.shape)
            hdftools.append_hdf(hdf, data)
            hdf["configs"][:, :, :] = configs.configs


from pyqmc.mc import limdrift
def sample_overlap(wfs,
    configs,
    pgrad,
    nsteps=100,
    tstep=0.1,
    hdf_file=None,
):
    r"""
    Sample 

    .. math:: \rho(R) = \sum_i |\Psi_i(R)|^2

    `pgrad` is expected to be a gradient generator. returns data as follows:

    `overlap` : 

    .. math:: \left\langle \frac{\Psi_i^* \Psi_j}{\rho} \right\rangle

    `overlap_gradient`:

    .. math:: \left\langle \frac{\partial_{im} \Psi_i^* \Psi_j}{\rho} \right\rangle

    In addition, any key returned by `pgrad` will be saved with an additional index at the beginning that indicates the wave function.
    """
    nconf, nelec, ndim = configs.configs.shape

    for wf in wfs:
        wf.recompute(configs)

    return_data = {}
    for step in range(nsteps):
        #print("step", step)
        for e in range(nelec):
            # Propose move
            grads = [np.real(wf.gradient(e,configs.electron(e)).T) for wf in wfs]
            
            grad = limdrift(np.mean(grads, axis = 0))
            gauss = np.random.normal(scale=np.sqrt(tstep), size=(nconf, 3))
            newcoorde = configs.configs[:, e, :] + gauss + grad * tstep
            newcoorde = configs.make_irreducible(e, newcoorde)

            # Compute reverse move
            grads = [np.real(wf.gradient(e,newcoorde).T) for wf in wfs]
            new_grad = limdrift(np.mean(grads, axis=0))
            forward = np.sum(gauss ** 2, axis=1)
            backward = np.sum((gauss + tstep * (grad + new_grad)) ** 2, axis=1)

            # Acceptance
            t_prob = np.exp(1 / (2 * tstep) * (forward - backward))
            wf_ratios = np.array([wf.testvalue(e,newcoorde)**2 for wf in wfs])
            log_values = np.array([wf.value()[1] for wf in wfs])
            ref = log_values[0]
            weights = np.exp(2*(log_values-ref))

            ratio = t_prob*np.sum(wf_ratios * weights, axis=0)/np.sum(weights, axis=0)
            accept = ratio > np.random.rand(nconf)

            # Update wave function
            configs.move(e, newcoorde, accept)
            for wf in wfs:
                wf.updateinternals(e, newcoorde, mask=accept)
            #print("accept", np.mean(accept))

        log_values = np.array([wf.value() for wf in wfs])
        #print(log_values.shape)
        ref = np.max(log_values[:,1,:],axis=0)
        save_dat = {}
        denominator = np.sum(np.exp(2*(log_values[:,1,:]-ref)),axis=0)
        normalized_values = log_values[:,0,:]*np.exp(log_values[:,1,:] - ref)
        save_dat['overlap'] = np.mean(np.einsum("ik,jk->ijk",normalized_values,normalized_values)/denominator, axis = -1)
        weight = np.array([np.exp(-2*(log_values[i,1,:]-log_values[:,1,:])) for i in range(len(wfs))])
        weight = 1.0/np.sum(weight,axis=1)
        #print(weight)
        dats = [pgrad(configs,wf) for wf in wfs]
        dppsi = np.array([dat['dppsi'] for dat in dats])
        save_dat['overlap_gradient']= np.mean(np.einsum("ikm,ik,jk->ijmk",dppsi, normalized_values,normalized_values)/denominator, axis=-1)
        for k in dats[0].keys():
            save_dat[k] = np.array([np.average(dat[k],axis=0, weights=w) for dat,w in zip(dats, weight)])
        save_dat['weight'] = np.mean(weight,axis=1)
        #print(save_dat['total'], save_dat['weight'])
        for k, it in save_dat.items():
            if k not in return_data:
                return_data[k] = np.zeros((nsteps, *it.shape))
            return_data[k][step,...] = it.copy()
    return return_data
            


def correlated_sample(wfs, configs, parameters, pgrad):
    r"""
    Given a configs sampled from the distribution
    
    .. math:: \rho = \sum_i \Psi_i^2

    Compute properties for replacing the last wave function with each of parameters

    For energy

    .. math:: \langle E \rangle = \left\langle \frac{H\Psi}{\Psi} \frac{|\Psi|^2}{\rho}  \right\rangle

    The ratio can be computed as 

    .. math:: \frac{|\Psi|^2}{\rho} = \frac{1}{\sum_i e^{\alpha_i - \alpha} 

    Where we write 

    .. math:: \Psi = e^{i\theta}{e^\alpha} 

    We also compute 

    .. math:: \langle S_i \rangle = \left\langle \frac{\Psi_i^* \Psi}{\rho} \right\rangle

    And 

    .. math:: \langle N_i \rangle = \left\langle \frac{|\Psi_i|^2}{\rho} \right\rangle

    """
    p0 = pgrad.transform.serialize_parameters(wfs[-1].parameters)
    log_values0 = np.array([wf.value() for wf in wfs])
    nparms = len(parameters)
    nconfig = configs.configs.shape[0]
    ref = np.max(log_values0[:,1,:])
    normalized_values = log_values0[:,0,:]*np.exp(log_values0[:,1,:] - ref)
    denominator = np.sum(np.exp(2*(log_values0[:,1,:]-ref)),axis=0)

    weight = np.array([np.exp(-2*(log_values0[i,1,:]-log_values0[:,1,:])) for i in range(len(wfs))])
    weight = np.mean(1.0/np.sum(weight,axis=1), axis=1)
    print('weight', weight.shape)

    data = {'total':np.zeros(nparms),
            'weight':np.zeros(nparms),
            'overlap':np.zeros((nparms,len(wfs))) } 
    data['base_weight'] = weight
    for p,parameter in enumerate(parameters): 
        #print(parameter)
        wf = wfs[-1]
        for k, it in pgrad.transform.deserialize(parameter).items():
            wf.parameters[k] = it
        wf.recompute(configs)
        val = wf.value()
        dat = pgrad(configs,wf)
        #print(log_values0.shape, val[1].shape)
    
        wt = 1./np.sum(np.exp(2*log_values0[:,1,:]-2*val[1][np.newaxis,:]),axis=0)
        normalized_val = val[0]*np.exp(val[1]-ref)
        # This is the new rho with the test wave function 
        rhoprime = (np.sum(np.exp(2*log_values0[0:-1,1,:] -2*ref),axis=0)+np.exp(2*val[1]-2*ref))/denominator

        overlap = np.einsum("k,jk->jk",normalized_val,normalized_values)/denominator

        data['total'][p] = np.average(dat['total'],weights= wt)
        data['weight'][p] = np.mean(wt)/np.mean(rhoprime)
        print(np.mean(wt), weight, np.mean(rhoprime))
        data['overlap'][p] = np.mean(overlap,axis=1)/np.sqrt(np.mean(wt)*weight)
        
        #print('wt', wt)
    #print('average energy',data['total'])
    for k, it in pgrad.transform.deserialize(p0).items():
        wfs[-1].parameters[k] = it
    return data



def optimize_orthogonal(wfs, coords, pgrad, tstep=0.01, nsteps=30, forcing = 10.0,
    warmup = 5,
    Starget = 0.0,
    Ntarget = 0.5,
    forcing_N = None,
    max_step = 10.0,
    alpha_mix = .5,
    hdf_file =None,
    update_method = 'linemin'
):
    r"""
    Minimize 

    .. math:: f(p_f) = E_f + \sum_i \lambda_{i=0}^{f-1} (S_{fi} - S_{fi}^*)^2 + \lambda_{norm} (N_f - N_f^*)^2

    Where 

    .. math:: N_i = \langle \Psi_i | \Psi_j \rangle

    .. math:: S_{fi} = \frac{\langle \Psi_f | \Psi_j \rangle}{\sqrt{N_f N_i}}

    The *'d and lambda values are respectively targets and forcings. f is the final wave function in the wave function array.
    We only optimize the parameters of the final wave function, so all 'p' values here represent a parameter in the final wave function. 

    The derivatives are:

    .. math:: \partial_p N_f = 2 Re \langle \partial_p \Psi_f | \Psi_f \rangle

    .. math::  \langle \partial_p \Psi_f | \Psi_f \rangle = \int{ \frac{ \Psi_f\partial_p \Psi_f^*}{\rho} \frac{\rho}{\int \rho} } 

    .. math:: \partial_p S_{fi} = \frac{\langle \partial_p \Psi_f | \Psi_j \rangle}{\sqrt{N_f N_i}} - \frac{\langle \Psi_f | \Psi_j \rangle}{\sqrt{N_f N_i}} \frac{\partial_p N_f}{N_f} 

    In this implementation, we set 

    .. math:: \rho = \sum_i |\Psi_i|^2

    Note that in the definition of N there is an arbitrary normalization of rho. The targets are set relative to the normalization of the reference wave functions. 
    """
    if forcing_N is None:
        forcing_N = 1*forcing
    parameters = pgrad.transform.serialize_parameters(wfs[1].parameters)
    last_change = np.zeros(parameters.shape)
    beta1 = 0.9
    beta2=0.999
    adam_m = np.zeros(parameters.shape)
    adam_v = np.zeros(parameters.shape)
    adam_epsilon = 1e-8
    attr = dict(tstep=tstep, nsteps=nsteps, forcing=forcing, warmup=warmup,
    Starget=Starget, Ntarget=Ntarget, forcing_N=forcing_N, max_step=max_step, alpha_mix=alpha_mix)
    for step in range(nsteps): 
        return_data = sample_overlap(wfs,coords, pgrad)
        avg_data = {}
        for k, it in return_data.items():
            avg_data[k] = np.average(it[warmup:,...], axis=0)
        
        N = avg_data['overlap'].diagonal()
        N_derivative = 2*np.real(avg_data['overlap_gradient'].diagonal()).T
        print(N_derivative.shape)

        Nij = np.outer(N,N)
        S = avg_data['overlap']/np.sqrt(Nij)
        S_derivative = avg_data['overlap_gradient']/Nij[:,:,np.newaxis] - np.einsum('ij,im->ijm', avg_data['overlap']/Nij, N_derivative/N[:,np.newaxis])


        energy_derivative = 2.0*(avg_data['dpH']-avg_data['total'][:,np.newaxis]*avg_data['dppsi'])
        print(energy_derivative.shape)
        dp = avg_data['dppsi'][-1,...]
        condition = np.real(avg_data['dpidpj'][-1,...]- np.einsum('i,j->ij',dp,dp))

        #assume for the moment we just have two wave functions and the base is 0
        total_derivative = energy_derivative[1,:] + 2.0*forcing * (S[1,0] -Starget)*S_derivative[1,0,:] + 2.0*forcing_N*(N[1]-Ntarget)*N_derivative[1,:]
        print("derivative", total_derivative)
        print("N derivative", 2.0*forcing_N*(N[1]-Ntarget)*N_derivative[1,:])

        deriv_norm = np.linalg.norm(total_derivative)
        if deriv_norm > max_step:
            total_derivative = total_derivative*max_step/deriv_norm
        this_change = alpha_mix*last_change + pyqmc.linemin.sr_update(total_derivative, condition, tstep)

        test_parameters=[]
        test_tsteps = np.linspace(-tstep,tstep, 10)
        for tmp_tstep in test_tsteps:
            test_parameters.append(parameters + pyqmc.linemin.sr_update(total_derivative, condition, tmp_tstep))
            #test_parameters.append(parameters - tmp_tstep * total_derivative)

        data = correlated_sample(wfs, coords, test_parameters, pgrad)
        yfit = []
        print('test_tsteps', test_tsteps)
        print('energy', 'S', 'weight', 'cost function')
        for enp, Sp, Np in zip(data['total'],data['overlap'],data['weight']):
            cost = enp + forcing*(Sp[0]-Starget)**2 + forcing_N*(Np-Ntarget)**2
            print(enp, Sp[0], Np, cost)
            yfit.append(cost)

        min_tstep = pyqmc.linemin.stable_fit(test_tsteps, yfit)

        if update_method == 'linemin':
            parameters +=  pyqmc.linemin.sr_update(total_derivative, condition, min_tstep)
        elif update_method == 'momentum':
            parameters += this_change
        elif update_method =='adam':
            adam_m = beta1*adam_m + (1-beta1)*total_derivative
            adam_v = beta2*adam_v + (1-beta2)*total_derivative**2
            adam_mhat = adam_m/(1-beta1**(step+1))
            adam_vhat = adam_v/(1-beta2**(step+1))
            parameters -= tstep*adam_mhat/(np.sqrt(adam_vhat)+adam_epsilon)
        else:
            raise ValueError("update_method not implemented:" + update_method)

        for k, it in pgrad.transform.deserialize(parameters).items():
            wfs[1].parameters[k] = it
        last_change = this_change

        print("energies", avg_data['total'])
        print("Normalization", N, 'target', Ntarget)
        print("overlap", S[1,0], 'target', Starget )
        save_data = {'energies': avg_data['total'],
                    'overlap': S,
                    'gradient': total_derivative,
                    'N' : N,
                    'parameters': parameters}
        print('Determinant coefficients', wfs[-1].parameters['wf1det_coeff'])

        ortho_hdf(hdf_file, save_data, attr, coords)


            


if __name__ =="__main__":
    import pyscf
    import pyqmc
    import pandas as pd
    import copy
    mol = pyscf.gto.M(atom = "He 0. 0. 0.", basis='bfd_vdz', ecp='bfd', unit='bohr')

    mf = pyscf.scf.RHF(mol).run()
    mol.output = None
    mol.stdout = None
    mf.output = None
    mf.stdout = None
    mf.chkfle = None


    wf = pyqmc.slater_jastrow(mol, mf)

    nconfig = 4000
    acc = pyqmc.gradient_generator(mol, wf)
    configs = pyqmc.initial_guess(mol, nconfig)
    wf, linedata = pyqmc.line_minimization(wf, configs, acc)
    wf2 = copy.deepcopy(wf)
    wf2.parameters['wf1mo_coeff_alpha'] += .01*np.random.randn(*wf2.parameters['wf1mo_coeff_alpha'].shape)
    wf2.parameters['wf1mo_coeff_beta'] += .01*np.random.randn(*wf2.parameters['wf1mo_coeff_beta'].shape)

    for starget in [1.0, 0.8, 0.6, 0.4, 0.2, 0.0, -0.2, -0.4, -0.6, -0.8]:
        optimize_orthogonal([wf,wf2], configs, acc, nsteps = 50, hdf_file = f'nconfig4000ortho{starget}.hdf5', Starget = starget)
    #avg_data = {}
    #for k, it in return_data.items():
    #    avg_data[k] = np.mean(it, axis=0)
    #for k, it in avg_data.items():
    #    print(k,it.shape)
    #Nij = np.outer(avg_data['overlap'].diagonal(), avg_data['overlap'].diagonal())
    #avg_data['overlap'] /= np.sqrt(Nij)
    #avg_data['overlap_gradient'] /= np.sqrt(Nij[..., np.newaxis])
    #actual_derivative = avg_data['overlap_gradient'] - np.einsum('ij,iim->ijm', avg_data['overlap'], 2*avg_data['overlap_gradient'])
    #print("actual derivative", actual_derivative)


