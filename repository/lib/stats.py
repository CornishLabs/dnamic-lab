import numpy as np
from scipy.special import betaincinv, betaln
import matplotlib.pyplot as plt
from math import erf, sqrt

def jeffreys_median_ci(y, n, level=0.6827):
    """
    Jeffreys posterior for binomial: p | y,n ~ Beta(y+0.5, n-y+0.5)
    Returns median and equal-tailed CI at the given level.
    Vectorized over y,n (arrays or scalars).

    Parameters
    ----------
    y : array_like  (# successes)
    n : array_like  (# trials)
    level : float   central mass (e.g. 0.6827 ≈ 1σ, 0.95, 0.99)

    Returns
    -------
    median, lo, hi : np.ndarray
        median is the 50th percentile; lo/hi are absolute bounds.
    """
    y = np.asarray(y, dtype=float)
    n = np.asarray(n, dtype=float)
    a = y + 0.5
    b = n - y + 0.5

    # median (q=0.5)
    median = betaincinv(a, b, 0.5)

    # equal-tailed interval
    alpha = 1.0 - float(level)
    lo = betaincinv(a, b, alpha/2.0)
    hi = betaincinv(a, b, 1.0 - alpha/2.0)

    return median, lo, hi

# TODO: tidy up API

def moment_matched_beta_for_average(y, n, w=None, prior=(0.5, 0.5), drift_aware=False):
    """
    Build Beta(alpha*, beta*) approximating the posterior of:
      bar_p = sum_i w_i p_i, where p_i | data ~ Beta(y_i+alpha0, n_i-y_i+beta0).
    If drift_aware=True, include between-run variance (random-time/SD-like).
    Returns (alpha_star, beta_star, mean m, variance v_used).
    """
    y = np.asarray(y, float)
    n = np.asarray(n, float)
    a0, b0 = prior
    a = y + a0
    b = n - y + b0

    mu  = a / (a + b)
    var = (a * b) / ((a + b)**2 * (a + b + 1.0))

    if w is None:
        w = np.ones_like(mu) / len(mu)
    else:
        w = np.asarray(w, float)
        w = w / np.sum(w)

    m = float(np.sum(w * mu))
    v_within  = float(np.sum((w**2) * var))
    v_between = float(np.sum(w * (mu - m)**2))
    v = v_within + (v_between if drift_aware else 0.0)

    # Guard rails
    m = float(np.clip(m, 1e-12, 1 - 1e-12))
    v = float(np.clip(v, 1e-18, m*(1-m) - 1e-18))

    kappa = m*(1-m)/v - 1.0
    alpha_star = m * kappa
    beta_star  = (1 - m) * kappa
    return alpha_star, beta_star, m, v

def pooled_posterior_beta(y, n, prior=(0.5, 0.5)):
    """Constant-p assumption: pool counts, apply Jeffreys (or given) prior once."""
    a0, b0 = prior
    return a0 + float(np.sum(y)), b0 + float(np.sum(n - y))


def beta_quartiles(a,b):
    return (float(betaincinv(a,b,0.25)),
            float(betaincinv(a,b,0.50)),
            float(betaincinv(a,b,0.75)))

# ---------- main plotting demo ----------

def plot_combined_posteriors(
    r=6,                       # number of runs
    n=60,                      # shots per run (int or array of length r)
    mu_true=0.30,              # true mean of latent p
    sd_true=0.02,              # true sd of latent p
    level=0.6827,              # credible mass to “eyeball” (not drawn as bands here)
    seed=0,                    # RNG seed
    weights="shots",           # "shots" (n_i / sum n) or "equal"
):
    

    def truncated_normal_pdf(p, m, s, a=0.0, b=1.0):
        """Density of N(m,s^2) truncated to [a,b] (vectorized)."""
        p = np.asarray(p)
        z = (p - m)/s
        Z = 0.5*(erf((b - m)/(s*sqrt(2))) - erf((a - m)/(s*sqrt(2))))
        base = (1.0/(s*np.sqrt(2*np.pi))) * np.exp(-0.5*z*z)
        dens = np.where((p >= a) & (p <= b), base/np.maximum(Z, 1e-300), 0.0)
        return dens
    
    def beta_pdf_on_grid(a, b, grid):
        """Stable Beta density on a grid via log-space normalization."""
        logpdf = (a - 1.0)*np.log(grid) + (b - 1.0)*np.log1p(-grid) - (betaln(a, b))
        logpdf -= np.max(logpdf)  # for numerical stability
        f = np.exp(logpdf)
        f /= np.trapezoid(f, grid)
        return f

    rng = np.random.default_rng(seed)
    if np.isscalar(n):
        n_i = np.full(r, int(n))
    else:
        n_i = np.asarray(n, int)
        assert len(n_i) == r, "n must be scalar or length r"

    # Simulate latent p_i and observations
    p_latent = np.clip(rng.normal(mu_true, sd_true, size=r), 1e-12, 1 - 1e-12)
    y_i = rng.binomial(n_i, p_latent)

    # Per-run Jeffreys posteriors
    a_i = y_i + 0.5
    b_i = n_i - y_i + 0.5

    # Weights for averaging
    if weights == "shots":
        w = n_i / n_i.sum()
    elif weights == "equal":
        w = np.ones(r) / r
    else:
        raise ValueError("weights must be 'shots' or 'equal'")

    # Grid
    p = np.linspace(1e-6, 1 - 1e-6, 14000)

    # Per-run densities (for lower plot)
    per_run = [beta_pdf_on_grid(ai, bi, p) for ai, bi in zip(a_i, b_i)]

    # ---------- Combined distributions ----------
    # (1) Constant-p: pooled counts -> Beta
    A, B = pooled_posterior_beta(y_i, n_i, prior=(0.5, 0.5))
    f_const = beta_pdf_on_grid(A, B, p)

    # (2) Weighted average WITHOUT drift (SE-like)
    a_star, b_star, m_no, v_no = moment_matched_beta_for_average(
        y_i, n_i, w=w, prior=(0.5, 0.5), drift_aware=False
    )
    f_avg_no = beta_pdf_on_grid(a_star, b_star, p)

    # (3a) Weighted average WITH drift (matched single Beta)
    a_d, b_d, m_d, v_d = moment_matched_beta_for_average(
        y_i, n_i, w=w, prior=(0.5, 0.5), drift_aware=True
    )
    f_avg_d = beta_pdf_on_grid(a_d, b_d, p)

    # (3b) Weighted average WITH drift — exact mixture of Betas
    f_avg_d_unmatched = np.sum((w[:, None] * np.vstack(per_run)), axis=0)

    # True latent density (for reference)
    f_true = truncated_normal_pdf(p, mu_true, sd_true)

    # ---------- NEW: per-run median of each posterior ----------
    med_i = betaincinv(a_i, b_i, 0.5)

    # ---------- Plots (now 3 rows) ----------
    fig, axes = plt.subplots(
        3, 1, figsize=(9, 9), sharex=True,
        gridspec_kw={"height_ratios": [3, 2, 2]}
    )

    # Top: true vs combined options
    ax = axes[0]
    ax.plot(p, f_true, label=f"True latent p ~ N({mu_true:.2f}, {sd_true:.3g}²) [truncated]")
    ax.plot(p, f_const, label=f"Constant p (pooled)  Beta({A:.1f},{B:.1f})")
    ax.plot(p, f_avg_no, label=f"Weighted average (no drift)  Beta({a_star:.1f},{b_star:.1f})")
    ax.plot(p, f_avg_d,  label=f"Weighted average (drift-aware)  Beta({a_d:.1f},{b_d:.1f})")
    ax.plot(p, f_avg_d_unmatched, label="Weighted average (drift-aware)  Mixture of Betas")
    ax.plot(p, f_e, label="MLE")
    ax.set_ylabel("density")
    ax.set_title("Overall distributions over p")
    ax.legend(loc="best")
    ax.grid(alpha=0.2)

    # Middle: per-run posteriors
    ax = axes[1]
    for k, f in enumerate(per_run, 1):
        ax.plot(p, f, alpha=0.75)
        ax.axvline(p_latent[k-1], linestyle=":", alpha=0.35)
    ax.set_ylabel("density")
    ax.set_title("Per-run posteriors (Jeffreys); dotted = latent p_i")
    ax.grid(alpha=0.2)

    # Bottom: NEW histogram of per-run medians (with latent p_i overlay)
    ax = axes[2]
    ax.hist(med_i, bins=20, density=True, alpha=0.6, label="Posterior medians per run")
    ax.hist(p_latent, bins=20, density=True, alpha=0.4, label="Latent p_i (simulation)", histtype="stepfilled")
    ax.axvline(mu_true, color="k", linestyle="--", alpha=0.6, label="mu_true")
    ax.set_xlabel("p")
    ax.set_ylabel("density")
    ax.set_title("Histogram of per-run posterior medians")
    ax.legend(loc="best")
    ax.grid(alpha=0.2)

    plt.tight_layout()
    plt.show()

    # Print a quick summary
    med = lambda a,b: betaincinv(a,b,0.5)
    print("Counts per run (y/n):", list(zip(y_i.tolist(), n_i.tolist())))
    print("Weights used:", "shots (n_i/Σn)" if weights=="shots" else "equal (1/r)")
    print("\nMedians (top curves):")
    print("  Constant p           :", float(med(A,B)))
    print("  Avg (no drift)       :", float(med(a_star,b_star)))
    print("  Avg (drift: matched) :", float(med(a_d,b_d)))
    # If you want the mixture’s median too:
    # mixture CDF via trapz, then inverse by interpolation
    cdf_mix = np.cumtrapz(f_avg_d_unmatched, p, initial=0.0)
    cdf_mix /= cdf_mix[-1]
    mix_median = float(np.interp(0.5, cdf_mix, p))
    print("  Avg (drift: mixture) :", mix_median)

# ---------- example ----------
if __name__ == "__main__":
    plot_combined_posteriors(
        r=200, n=800, mu_true=0.75, sd_true=0.0003,
        level=0.6827, seed=None, weights="shots"
    )

