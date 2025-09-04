import math
import numpy as np

from common import *


def _q_normalize(q):
    return q / np.linalg.norm(q)

def _q_mul(q1, q2):
    w1, x1, y1, z1 = q1
    w2, x2, y2, z2 = q2
    return np.array([
        w1*w2 - x1*x2 - y1*y2 - z1*z2,
        w1*x2 + x1*w2 + y1*z2 - z1*y2,
        w1*y2 - x1*z2 + y1*w2 + z1*x2,
        w1*z2 + x1*y2 - y1*x2 + z1*w2
    ], dtype=np.float32)

def _axis_angle_to_q(axis, angle):
    ha = 0.5*angle
    s = math.sin(ha)
    return np.array([math.cos(ha), axis[0]*s, axis[1]*s, axis[2]*s], dtype=np.float32)

def _q_conj(q):
    return np.array([q[0], -q[1], -q[2], -q[3]], dtype=np.float32)

def _rotate(q, v):
    # Rotate vector v (3,) by quaternion q (w,x,y,z); returns (3,)
    qv = np.array([0.0, v[0], v[1], v[2]], dtype=np.float32)
    return _q_mul(_q_mul(q, qv), _q_conj(q))[1:]

def _init_from_acc_mag(acc, mag):
    """Initialize quaternion from accelerometer (roll/pitch) and magnetometer (yaw)."""
    a = np.asarray(acc, dtype=np.float32)
    m = np.asarray(mag, dtype=np.float32)
    # roll/pitch from acc
    ax, ay, az = a / (np.linalg.norm(a) + 1e-12)
    roll  = math.atan2(ay, az)
    pitch = math.atan2(-ax, math.sqrt(ay*ay + az*az))
    # yaw from mag (after removing roll/pitch tilt)
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    mx, my, mz = m / (np.linalg.norm(m) + 1e-12)
    # Rotate body mag into leveled frame (remove roll/pitch)
    mx_l =  cp*mx + sp*sr*my + sp*cr*mz
    my_l =       cr*my - sr*mz
    yaw  = math.atan2(-my_l, mx_l)  # heading towards magnetic north
    # Combine Euler angles → quaternion (Z-Y-X)
    cy, sy = math.cos(yaw/2),   math.sin(yaw/2)
    cp2, sp2 = math.cos(pitch/2), math.sin(pitch/2)
    cr2, sr2 = math.cos(roll/2),  math.sin(roll/2)
    w = cy*cp2*cr2 + sy*sp2*sr2
    x = cy*cp2*sr2 - sy*sp2*cr2
    y = cy*sp2*cr2 + sy*cp2*sr2
    z = sy*cp2*cr2 - cy*sp2*sr2
    return _q_normalize(np.array([w, x, y, z], dtype=np.float32))


class Mahony9D:
    """
    Mahony 9D (gyro+acc+mag).
    Gravity vector (acc) and magnetic vector (mag) form the corrections.
    Gyro is integrated, gyro bias is estimated by an integrator.
    """
    def __init__(self,
                 kp_acc=2.0, ki_acc=0.05,
                 kp_mag=1.5, ki_mag=0.02,
                 use_bias=True,
                 acc_tol=0.15,           # allowed relative deviation of |a| from g
                 mag_uT_range=(20.0, 70.0)):  # accepted |m| in µT (typical Earth field ~25–65)
        self.q = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        self.bias = np.zeros(3, dtype=np.float32)
        self.kp_acc, self.ki_acc = float(kp_acc), float(ki_acc)
        self.kp_mag, self.ki_mag = float(kp_mag), float(ki_mag)
        self.use_bias = bool(use_bias)
        self.acc_tol = float(acc_tol)
        self.mag_min, self.mag_max = mag_uT_range

    def reset(self, acc=None, mag=None):
        if acc is not None and mag is not None:
            self.q = _init_from_acc_mag(acc, mag)
        elif acc is not None:
            # Fallback without mag
            a = np.asarray(acc, dtype=np.float32)
            ax, ay, az = a / (np.linalg.norm(a) + 1e-12)
            roll  = math.atan2(ay, az)
            pitch = math.atan2(-ax, math.sqrt(ay*ay + az*az))
            cy, sy = 1.0, 0.0
            cr, sr = math.cos(roll/2),  math.sin(roll/2)
            cp, sp = math.cos(pitch/2), math.sin(pitch/2)
            w = cy*cp*cr + sy*sp*sr
            x = cy*cp*sr - sy*sp*cr
            y = cy*sp*cr + sy*cp*sr
            z = sy*cp*cr - cy*sp*sr
            self.q = _q_normalize(np.array([w, x, y, z], dtype=np.float32))
        else:
            self.q[:] = [1.0, 0.0, 0.0, 0.0]
        self.bias[:] = 0.0

    def update(self, gyro_dps, acc_ms2, mag_uT, dt):
        """
        gyro_dps ... [gx, gy, gz] in deg/s
        acc_ms2  ... [ax, ay, az] in m/s^2
        mag_uT   ... [mx, my, mz] in microtesla
        dt       ... timestep in seconds (e.g. 0.01 for 100 Hz)
        Returns quaternion [w, x, y, z] (float32).
        """
        g = np.asarray(gyro_dps, dtype=np.float32) * (math.pi/180.0)  # rad/s
        a = np.asarray(acc_ms2, dtype=np.float32)
        m = np.asarray(mag_uT,  dtype=np.float32)

        # 0) Integrate gyro (with bias compensation)
        omega = g - self.bias
        ang = np.linalg.norm(omega) * dt
        if ang > 1e-9:
            dq = _axis_angle_to_q(omega / (np.linalg.norm(omega) + 1e-12), ang)
            self.q = _q_normalize(_q_mul(self.q, dq))

        # Accumulate error from acc and mag → virtual angular velocity
        e_total = np.zeros(3, dtype=np.float32)

        # 1) Accelerometer correction (gravity vector)
        a_norm = np.linalg.norm(a)
        if a_norm > 1e-6:
            if abs(a_norm - G0) / G0 <= self.acc_tol:
                a_hat = a / a_norm  # measured gravity (down) in body frame
                g_est = _rotate(self.q, np.array([0.0, 0.0, -1.0], dtype=np.float32))
                e_acc = np.cross(g_est, a_hat)
                e_total += self.kp_acc * e_acc
                if self.use_bias and self.ki_acc > 0.0:
                    self.bias += self.ki_acc * e_acc * dt

        # 2) Magnetometer correction (magnetic field direction)
        m_norm = np.linalg.norm(m)
        if m_norm > 1e-9 and (self.mag_min <= m_norm <= self.mag_max):
            m_hat = m / m_norm  # measured magnetic field direction in body frame
            # Estimate horizontal magnetic field in world frame
            h = _rotate(_q_conj(self.q), m_hat)    # body → world
            bx = math.sqrt(h[0]*h[0] + h[1]*h[1])  # horizontal component
            bz = h[2]                              # vertical component
            # Expected magnetic field direction in body frame from current orientation
            m_est = _rotate(self.q, np.array([bx, 0.0, bz], dtype=np.float32))
            m_est /= (np.linalg.norm(m_est) + 1e-12)
            e_mag = np.cross(m_est, m_hat)
            e_total += self.kp_mag * e_mag
            if self.use_bias and self.ki_mag > 0.0:
                self.bias += self.ki_mag * e_mag * dt

        # 3) Apply correction as small rotational velocity
        corr_ang = np.linalg.norm(e_total) * dt
        if corr_ang > 0.0:
            dq_corr = _axis_angle_to_q(e_total/(np.linalg.norm(e_total)+1e-12), corr_ang)
            self.q = _q_normalize(_q_mul(self.q, dq_corr))

        return self.q.copy()


class OrientationEstimator(Mahony9D):
    pass