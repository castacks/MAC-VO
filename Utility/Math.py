import torch
import numpy as np
import pypose as pp

def qinterp(qs, t, t_int):
    idxs = np.searchsorted(t, t_int)
    idxs0 = idxs-1
    idxs0[idxs0 < 0] = 0
    idxs1 = idxs
    idxs1[idxs1 == t.shape[0]] = t.shape[0] - 1
    q0 = qs[idxs0]
    q1 = qs[idxs1]
    tau = torch.zeros_like(t_int)
    dt = (t[idxs1]-t[idxs0])[idxs0 != idxs1]
    tau[idxs0 != idxs1] = (t_int-t[idxs0])[idxs0 != idxs1]/dt
    return slerp(q0, q1, tau)

def slerp(q0, q1, tau, DOT_THRESHOLD = 0.9995):
    """Spherical linear interpolation."""

    dot = (q0*q1).sum(dim=1)
    q1[dot < 0] = -q1[dot < 0]
    dot[dot < 0] = -dot[dot < 0]

    q = torch.zeros_like(q0)
    tmp = q0 + tau.unsqueeze(1) * (q1 - q0)
    tmp = tmp[dot > DOT_THRESHOLD]
    q[dot > DOT_THRESHOLD] = tmp / tmp.norm(dim=1, keepdim=True)

    theta_0 = dot.acos()
    sin_theta_0 = theta_0.sin()
    theta = theta_0 * tau
    sin_theta = theta.sin()
    s0 = (theta.cos() - dot * sin_theta / sin_theta_0).unsqueeze(1)
    s1 = (sin_theta / sin_theta_0).unsqueeze(1)
    q[dot < DOT_THRESHOLD] = ((s0 * q0) + (s1 * q1))[dot < DOT_THRESHOLD]
    return q / q.norm(dim=1, keepdim=True)

def gaussian_kernels(std_x: torch.Tensor, std_y: torch.Tensor, kernel_size: int):
    """
    creates gaussian kernel with side length `l` and a sigma of `sig`
    """
    ax = torch.linspace(-(kernel_size - 1) / 2., (kernel_size - 1) / 2., kernel_size, device=std_x.device).unsqueeze(0)
    ay = torch.linspace(-(kernel_size - 1) / 2., (kernel_size - 1) / 2., kernel_size, device=std_x.device).unsqueeze(0)
    gaussx = (-0.5 * ax.square() / std_x.square().unsqueeze(1)).exp()
    gaussy = (-0.5 * ay.square() / std_y.square().unsqueeze(1)).exp()

    gaussx = gaussx.unsqueeze(1)
    gaussy = gaussy.unsqueeze(2)
    kernel = gaussy * gaussx
    return kernel / torch.sum(kernel, dim=[1, 2], keepdim=True)


def gaussian_mixture_mean_var(batch_means: torch.Tensor, batch_vars: torch.Tensor, batch_prob: torch.Tensor,
                          prob_threshold: float = 1e-3) -> tuple[torch.Tensor, torch.Tensor]:
    """
    Calculate the mean of B gaussian mixtures, each with N subpolulation.
    batch_means: B x N torch.float, mean of each subpopulation
    batch_vars : B x N torch.float, variance of each subpopulation
    batch_prob : B x N torch.float, prob of each subpopulation, (batch_prob.sum(dim=1) == 1.).all()
    
    ---
    
    Returns
    batch_mixture_mean: B torch.float, mean of each mixture.
    batch_mixture_var : B torch.float, variance of each mixture.
    
    ---
    
    Ref: https://stats.stackexchange.com/questions/445231/compute-mean-and-variance-of-mixture-of-gaussians-given-mean-variance-of-compone
    """
    
    # Remove some low-probability subpopolation to improve robustness of estimation.
    batch_prob[batch_prob < prob_threshold] = 0.
    batch_prob = batch_prob / batch_prob.sum(dim=1, keepdim=True)
    
    calc_mean = (batch_means * batch_prob).sum(dim=1)
    calc_var = ((batch_vars + batch_means.square()) * batch_prob).sum(dim=1) - \
               calc_mean.square()
    
    return calc_mean, calc_var / 2


def interpolate_pose(Ps: pp.LieTensor, ts: torch.Tensor, ts_ev: torch.Tensor) -> pp.LieTensor:
    assert (ts[..., :-1] < ts[..., 1:]).all() # check ts is sorted and no duplication.

    P_container = torch.empty((*ts_ev.shape, 7), dtype=Ps.dtype)
    before_mask, after_mask = ts_ev < ts[..., 0], ts_ev > ts[..., -1]
    interp_mask = ~torch.logical_or(before_mask, after_mask)

    if before_mask.sum().item() > 0: P_container[before_mask] = Ps[..., 0, :]
    if after_mask.sum().item() > 0 : P_container[after_mask]  = Ps[..., -1, :]

    idx_ev_end   = torch.searchsorted(ts, ts_ev, right=False)
    idx_ev_start = idx_ev_end - 1

    P_seg_start: pp.LieTensor = Ps[..., idx_ev_start, :]    #type: ignore
    P_seg_end  : pp.LieTensor = Ps[..., idx_ev_end, :]  #type: ignore
    t_seg_start = ts[..., idx_ev_start]
    t_seg_end   = ts[..., idx_ev_end]
    t_ev_prop   = (ts_ev - t_seg_start) / (t_seg_end - t_seg_start)

    lie_algebra_diff =(P_seg_end @ P_seg_start.Inv()).Log()
    lie_type = lie_algebra_diff.ltype

    P_interp = pp.LieTensor(t_ev_prop.unsqueeze(-1) * lie_algebra_diff, ltype=lie_type).Exp() @ P_seg_start
    if interp_mask.sum().item() > 0: P_container[interp_mask] = P_interp
    return pp.SE3(P_container)


def NormalizeQuat(x: pp.LieTensor) -> pp.LieTensor:
    data, ltype = x.tensor(), x.ltype
    data[..., 3:] = data[..., 3:]/data[..., 3:].norm(dim=-1, keepdim=True)
    return pp.LieTensor(data, ltype=ltype)