# Semi-Manual Novelty Search
## Search Configuration
### IEEE Xplore
1. `"actuator degradation" AND bifurcation`
2. `"actuator fault" AND bifurcation AND control`
3. `"fault-induced bifurcation" AND nonlinear control`
4. `"actuator effectiveness" AND bifurcation`
5. `"incipient actuator fault" AND bifurcation`
6. `"latent dynamics" AND actuator fault AND control`

## Retrieval Statistics
### IEEE Xplore
- Query: `"actuator degradation" AND bifurcation`
  - Raw results: 0
  - Unique (within site): 0
- Query: `"actuator fault" AND bifurcation AND control`
  - Raw results: 3
  - Unique (within site): 3
- Query: `"fault-induced bifurcation" AND nonlinear control`
  - Raw results: 0
  - Unique (within site): 0
- Query: `"actuator effectiveness" AND bifurcation`
  - Raw results: 0
  - Unique (within site): 0
- Query: `"incipient actuator fault" AND bifurcation`
  - Raw results: 0
  - Unique (within site): 0
- Query: `"latent dynamics" AND actuator fault AND control`
  - Raw results: 0
  - Unique (within site): 0

## Summary
- **Total deduplicated papers:** 3
- **Papers with actual abstracts:** 3
- **Papers without abstracts:** 0

## Deduplicated Papers
---

### 1. Analysis and Recovery of Aircraft Deep-Stall Phenomena Using Bifurcation Analysis

**Authors:** Dawei Wu; Mou Chen; Hui Ye

**Year:** 2020

**Venue:** IEEE Access

**Publisher:** IEEE

**DOI:** 10.1109/access.2020.2972570

**DOI source:** api

**Sources:** ieee

**Queries:** "actuator fault" AND bifurcation AND control

**Paper URL:** https://ieeexplore.ieee.org/document/8988181/

**PDF URL:** https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=8988181

#### Abstract

In this paper, the problem of deep-stall recovery will be studied for the aircraft without longitudinal static stability. A new longitudinal short-period deep-stall model is established in the presence of time-varying distributed delays. Based on bifurcation analysis method, the effects of actuator fault on deep stall are analyzed in depth. Considering the system uncertainty, actuator fault, input saturation and unsteady disturbance, a finite-time prescribed performance deep-stall recovery law is designed according to the bifurcation analysis results. Further, to avoid the singularity problem in traditional finite-time control, an improved finite-time control law is developed. Stability of the closed-loop system is proved by common Lyapunov functional method. And simulations are given to illustrate the validity of the proposed deep-stall recovery control scheme.

**Abstract source:** api

---

### 2. Prescribed-Time Fault-Tolerant Control of the FO Decoupled Dual-Mass MEMS Gyro With Deferred Constraints-Design and Implementation

**Authors:** Shaohua Luo; Yongduan Song; Guangwei Deng; Junxing Zhang; Hassen M. Ouakad

**Year:** 2024

**Venue:** IEEE Transactions on Systems, Man, and Cybernetics: Systems

**Publisher:** IEEE

**DOI:** 10.1109/tsmc.2024.3427312

**DOI source:** api

**Sources:** ieee

**Queries:** "actuator fault" AND bifurcation AND control

**Paper URL:** https://ieeexplore.ieee.org/document/10614724/

**PDF URL:** https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=10614724

#### Abstract

This article mainly investigates the model design, field programmable gate array (FPGA) implementation, and prescribed-time fault-tolerant control of a fractional-order (FO) decoupled dual-mass micro-electro-mechanical system (MEMS) gyro with deferred constraints. First, the structure of such MEMS gyro is designed to eliminate the linear acceleration in the sensing direction and its mathematical model is built based on the Lagrange’s equation. The dynamical analysis shows that such gyro can generate unpredictable, random, and disorder motions under various FOs, stiffness cross the coupling coefficients and proof masses. The designed FPGA circuit further demonstrates the undesirable chaotic oscillations of such MEMS gyro and good hardware resources utilization, avoiding the time consuming and board redesign. Second, to better solve the problems of constraints, actuator faults, uncertainties, drive couplings, and chaotic oscillations, a dependent deferred-error function superimposed to a prescribed-time function is used to guarantee no violation of constraints after a finite time. Furthermore, a  $\beta $ -cut type-2 fuzzy logic system (T2FLS) is employed to solve the uncertainty, and an FO hyperbolic tangent tracking differentiator (HTTD) is utilized to deal with the direct FO derivative and repeated derivative in the framework of the backstepping control. Then, a prescribed-time fault-tolerant control scheme of the FO decoupled dual-mass MEMS gyro is proposed under the actuator fault. Finally, the abundant simulation experimental results verify the feasibility and effectiveness of our scheme.

**Abstract source:** api

---

### 3. Dynamic Analysis and Neural-Adaptive Prescribed-Time Control of the FO Memristive Magnetic-Field Electromechanical Transducer

**Authors:** Shaohua Luo; Yongduan Song; Ya Zhang; Hassen M. Ouakad; Frank L. Lewis

**Year:** 2025

**Venue:** IEEE Transactions on Cybernetics

**Publisher:** IEEE

**DOI:** 10.1109/tcyb.2024.3474248

**DOI source:** api

**Sources:** ieee

**Queries:** "actuator fault" AND bifurcation AND control

**Paper URL:** https://ieeexplore.ieee.org/document/10722854/

**PDF URL:** https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=10722854

#### Abstract

This article is concerned with dynamic analysis and neural-adaptive prescribed-time control of the magnetic-field electromechanical transducer incorporating a memristor. First, a fractional-order (FO) mathematical model is developed, which comprehensively characterizes fractional properties of various dielectrics and establishes the relationship between magnetic flux and electric charge. The dynamical analysis explores internal evolution and complexity performance concerning a single factor or double factors among the FO, system parameter, and memristor configuration by the Bifurcation diagram, sample entropy, and  $C_{0}$  complexity from multiple perspectives. Subsequently, a neural-adaptive prescribed-time control scheme is proposed to transform detrimental chaotic oscillations into orderly motions, achieve the pregiven tracking precision and accommodating both actuator fault and system uncertainty. The controller design consists of three key steps: 1) a deferred constraint function is imposed on the tracking error starting from anywhere to get assignable tracking precision within a specified time, ensuring collision avoidance; 2) a type-2 fuzzy wavelet neural network (FWNN) is utilized effectively to handle parameter perturbations and system uncertainties; and 3) a second-order FO tracking differentiator (TD) is utilized to address the “explosion of complexity” of traditional backstepping under actuator fault model. It is shown that the proposed scheme is able to ensure the boundness of all signals of the closed-loop system. Finally, extensive simulation experiments are conducted to validate the effectiveness and robustness of the rendered scheme.

**Abstract source:** api

