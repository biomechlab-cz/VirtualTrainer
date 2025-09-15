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
                 kp_acc=2.0, ki_acc=0.02,  # mírnější integrace z acc
                 kp_mag=1.2, ki_mag=0.00,  # mag bez integrátoru (bezpečnější)
                 use_bias=True,
                 acc_tol=0.05,  # ±5 % okolo g
                 mag_uT_range=(20.0, 70.0)):
        self.q = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        self.bias = np.zeros(3, dtype=np.float32)

        self.kp_acc, self.ki_acc = float(kp_acc), float(ki_acc)
        self.kp_mag, self.ki_mag = float(kp_mag), float(ki_mag)
        self.use_bias = bool(use_bias)
        self.acc_tol = float(acc_tol)
        self.mag_min, self.mag_max = mag_uT_range

        # EMA magnetometru (zůstává)
        self._ema_m = None
        self.mag_alpha = 0.2

        # --- nové parametry ---
        self.lin_a_gate_g = 0.20  # acc korekci povolit jen když |a + g_body_est| < 0.20 g
        self.lin_a_hard_g = 0.50  # nad 0.5 g akcelerometr úplně ignoruj
        self.gyro_high_rad = math.radians(200)  # nad 200 dps vypnout mag
        self.bias_leak = 0.002  # 0.2 %/s leak biasu směrem k 0
        self.bias_clip_rad = math.radians(20)  # bias clamp ±20 dps

    def update(self, gyro_dps, acc_ms2, mag_uT, dt):
        g = np.asarray(gyro_dps, dtype=np.float32) * (math.pi / 180.0)  # rad/s
        a = np.asarray(acc_ms2, dtype=np.float32)
        m = np.asarray(mag_uT, dtype=np.float32)

        # EMA mag
        if self._ema_m is None:
            self._ema_m = m.copy()
        else:
            self._ema_m = (1.0 - self.mag_alpha) * self._ema_m + self.mag_alpha * m
        m = self._ema_m

        # --- 0) Integrace gyra s biasem + leak/clip ---
        self.bias *= (1.0 - self.bias_leak * dt)  # anti-windup leak
        self.bias = np.clip(self.bias, -self.bias_clip_rad, self.bias_clip_rad)
        omega = g - self.bias

        ang = np.linalg.norm(omega) * dt
        if ang > 1e-9:
            dq = _axis_angle_to_q(omega / (np.linalg.norm(omega) + 1e-12), ang)
            self.q = _q_normalize(_q_mul(self.q, dq))

        # --- 1) Vyhodnocení "klidu" pomocí lineární akcelerace ---
        # acc měří specifickou sílu (-g + lineární a). Odhad g v těle:
        g_body_est = _rotate(self.q, np.array([0.0, 0.0, -G0], dtype=np.float32))  # (0,0,-g) → body
        lin_a = a - (-g_body_est)  # = a + g_body_est
        lin_a_g = np.linalg.norm(lin_a) / G0  # v jednotkách g

        # --- kumulace chyb ---
        e_total = np.zeros(3, dtype=np.float32)

        # --- 2) Akcelerometr (jen když je klid) ---
        a_norm = np.linalg.norm(a)
        use_acc = (
                a_norm > 1e-6 and
                abs(a_norm - G0) / G0 <= self.acc_tol and
                lin_a_g < self.lin_a_gate_g
        )
        if use_acc:
            a_hat = -a / a_norm
            g_est = _rotate(self.q, np.array([0.0, 0.0, -1.0], dtype=np.float32))
            e_acc = np.cross(g_est, a_hat)

            # zisk škáluj podle klidu: čím menší lin_a_g, tím větší zisk (0.5–1.0×)
            acc_scale = 0.5 + 0.5 * max(0.0, 1.0 - (lin_a_g / self.lin_a_gate_g))
            e_total += (self.kp_acc * acc_scale) * e_acc

            if self.use_bias and self.ki_acc > 0.0:
                self.bias += self.ki_acc * e_acc * dt

        # --- 3) Magnetometr (vypnout při velké |ω| nebo když je pohyb) ---
        m_norm = np.linalg.norm(m)
        omega_norm = np.linalg.norm(omega)
        use_mag = (
                m_norm > 1e-9 and (self.mag_min <= m_norm <= self.mag_max) and
                (omega_norm < self.gyro_high_rad) and
                (lin_a_g < self.lin_a_hard_g)
        )
        if use_mag:
            m_hat = m / m_norm
            # tilt-kompenzace: odhad směru pole ve světovém rámci
            h = _rotate(_q_conj(self.q), m_hat)  # body → world
            bx = math.sqrt(h[0] * h[0] + h[1] * h[1])  # horizontální složka
            bz = h[2]  # vertikální složka
            m_est = _rotate(self.q, np.array([bx, 0.0, bz], dtype=np.float32))
            m_est /= (np.linalg.norm(m_est) + 1e-12)
            e_mag = np.cross(m_est, m_hat)

            # zisk škáluj podle rychlosti otáčení (víc tlumit při velké |ω|)
            mag_kp = self.kp_mag * (1.0 / (1.0 + (omega_norm / self.gyro_high_rad) ** 2))
            e_total += mag_kp * e_mag
            # bez integrátoru – méně rizika driftu z mag při rušení

        # --- 4) Aplikace korekce ---
        corr = np.linalg.norm(e_total)
        if corr > 0.0:
            dq_corr = _axis_angle_to_q(e_total / (corr + 1e-12), corr * dt)
            self.q = _q_normalize(_q_mul(self.q, dq_corr))

        # --- 5) Safety guard ---
        if not np.isfinite(self.q).all():
            self.q[:] = [1.0, 0.0, 0.0, 0.0]
            self.bias[:] = 0.0

        return self.q.copy()


class Madgwick9D:
    """
    Madgwick (gyro+acc+mag) s adaptivnim beta a gatingem podle lin. akcelerace a |ω|.
    API shodne s Mahony9D: update(gyro_dps, acc_ms2, mag_uT, dt) -> q[w,x,y,z]
    """
    def __init__(self,
                 beta_base=0.05, beta_max=0.35,         # adaptivní beta
                 acc_tol=0.05,                          # |a| v ±5 % okolo g
                 mag_uT_range=(20.0, 70.0),
                 lin_a_gate_g=0.20,                     # pro povolení acc korekce
                 lin_a_hard_g=0.50,                     # nad tuto hodnotu vypnout mag
                 gyro_high_rad=math.radians(200),       # ~200 dps
                 mag_alpha=0.2):
        self.q = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        self.beta_base = float(beta_base)
        self.beta_max  = float(beta_max)
        self.acc_tol   = float(acc_tol)
        self.mag_min, self.mag_max = mag_uT_range
        self.lin_a_gate_g = float(lin_a_gate_g)
        self.lin_a_hard_g = float(lin_a_hard_g)
        self.gyro_high_rad = float(gyro_high_rad)
        self._ema_m = None
        self.mag_alpha = float(mag_alpha)

    def reset(self, acc=None, mag=None):
        if acc is not None and mag is not None:
            self.q = _init_from_acc_mag(acc, mag)
        else:
            self.q[:] = [1.0, 0.0, 0.0, 0.0]

    def update(self, gyro_dps, acc_ms2, mag_uT, dt):
        q1, q2, q3, q4 = self.q  # w,x,y,z
        gx, gy, gz = (np.asarray(gyro_dps, np.float32) * (math.pi/180.0))
        ax, ay, az = np.asarray(acc_ms2, np.float32)
        mx, my, mz = np.asarray(mag_uT,  np.float32)

        # --- EMA magnetometru (opatrně vyhladit šum / rušení) ---
        if self._ema_m is None:
            self._ema_m = np.array([mx, my, mz], dtype=np.float32)
        else:
            self._ema_m = (1.0 - self.mag_alpha)*self._ema_m + self.mag_alpha*np.array([mx, my, mz], np.float32)
        mx, my, mz = self._ema_m

        # --- odhad lineární akcelerace (a + g_body_est) pro gating ---
        g_body_est = _rotate(self.q, np.array([0.0, 0.0, -G0], dtype=np.float32))  # (0,0,-g) v těle
        lin_a = np.array([ax, ay, az], np.float32) - (-g_body_est)
        lin_a_g = np.linalg.norm(lin_a) / G0
        omega_norm = float(np.linalg.norm([gx, gy, gz]))

        # --- normalizace senzorů + platnost ---
        a_norm = math.sqrt(ax*ax + ay*ay + az*az)
        m_norm = math.sqrt(mx*mx + my*my + mz*mz)
        use_acc = (a_norm > 1e-9 and abs(a_norm - G0)/G0 <= self.acc_tol and lin_a_g < self.lin_a_gate_g)
        use_mag = (m_norm > 1e-9 and (self.mag_min <= m_norm <= self.mag_max)
                   and omega_norm < self.gyro_high_rad and lin_a_g < self.lin_a_hard_g)

        if a_norm > 1e-9:
            ax, ay, az = ax/a_norm, ay/a_norm, az/a_norm
        if m_norm > 1e-9:
            mx, my, mz = mx/m_norm, my/m_norm, mz/m_norm

        # --- adaptivní beta (0 = klid, 1 = rychlý pohyb) ---
        alpha_move = max(min(lin_a_g/self.lin_a_gate_g, 1.0),
                         min(omega_norm/self.gyro_high_rad, 1.0))
        beta = self.beta_base + (self.beta_max - self.beta_base) * alpha_move

        # --- qDot z gyra ---
        qDot1 = 0.5 * (-q2*gx - q3*gy - q4*gz)
        qDot2 = 0.5 * ( q1*gx + q3*gz - q4*gy)
        qDot3 = 0.5 * ( q1*gy - q2*gz + q4*gx)
        qDot4 = 0.5 * ( q1*gz + q2*gy - q3*gx)

        # --- korekční krok (gradient descent) ---
        if use_acc:
            if use_mag:
                # 9D varianta (acc + mag)
                _2q1mx = 2.0*q1*mx
                _2q1my = 2.0*q1*my
                _2q1mz = 2.0*q1*mz
                _2q2mx = 2.0*q2*mx

                hx = 2.0*mx*(0.5 - q3*q3 - q4*q4) + 2.0*my*(q2*q3 - q1*q4) + 2.0*mz*(q2*q4 + q1*q3)
                hy = 2.0*mx*(q2*q3 + q1*q4)     + 2.0*my*(0.5 - q2*q2 - q4*q4) + 2.0*mz*(q3*q4 - q1*q2)
                _2bx = math.sqrt(hx*hx + hy*hy)
                _2bz = 2.0*mx*(q2*q4 - q1*q3) + 2.0*my*(q1*q2 + q3*q4) + 2.0*mz*(0.5 - q2*q2 - q3*q3)
                _4bx = 2.0*_2bx
                _4bz = 2.0*_2bz

                # zkrácené pomocné členy
                _2q1 = 2.0*q1; _2q2 = 2.0*q2; _2q3 = 2.0*q3; _2q4 = 2.0*q4
                _2q1q3 = 2.0*q1*q3; _2q3q4 = 2.0*q3*q4

                # gradient (s1..s4)
                s1 = (-_2q3*(2.0*(q2*q4 - q1*q3) - ax) + _2q2*(2.0*(q1*q2 + q3*q4) - ay)
                      - _2bz*q3*(_2bx*(0.5 - q3*q3 - q4*q4) + _2bz*(q2*q4 - q1*q3) - mx)
                      + (-_2bx*q4 + _2bz*q2)*( _2bx*(q2*q3 - q1*q4) + _2bz*(q1*q2 + q3*q4) - my)
                      + _2bx*q3*( _2bx*(q1*q3 + q2*q4) + _2bz*(0.5 - q2*q2 - q3*q3) - mz))
                s2 = ( _2q4*(2.0*(q2*q4 - q1*q3) - ax) + _2q1*(2.0*(q1*q2 + q3*q4) - ay) - 4.0*q2*(2.0*(0.5 - q2*q2 - q3*q3) - az)
                      + (_2bz*q4)*( _2bx*(0.5 - q3*q3 - q4*q4) + _2bz*(q2*q4 - q1*q3) - mx)
                      + (_2bx*q3 + _2bz*q1)*( _2bx*(q2*q3 - q1*q4) + _2bz*(q1*q2 + q3*q4) - my)
                      + (_2bx*q4 - _4bz*q2)*( _2bx*(q1*q3 + q2*q4) + _2bz*(0.5 - q2*q2 - q3*q3) - mz))
                s3 = (-_2q1*(2.0*(q2*q4 - q1*q3) - ax) + _2q4*(2.0*(q1*q2 + q3*q4) - ay) - 4.0*q3*(2.0*(0.5 - q2*q2 - q3*q3) - az)
                      + (-_4bx*q3 - _2bz*q1)*( _2bx*(0.5 - q3*q3 - q4*q4) + _2bz*(q2*q4 - q1*q3) - mx)
                      + (_2bx*q2 + _2bz*q4)*( _2bx*(q2*q3 - q1*q4) + _2bz*(q1*q2 + q3*q4) - my)
                      + (_2bx*q1 - _4bz*q3)*( _2bx*(q1*q3 + q2*q4) + _2bz*(0.5 - q2*q2 - q3*q3) - mz))
                s4 = ( _2q2*(2.0*(q2*q4 - q1*q3) - ax) + _2q3*(2.0*(q1*q2 + q3*q4) - ay)
                      + (-_2bz*q3)*( _2bx*(0.5 - q3*q3 - q4*q4) + _2bz*(q2*q4 - q1*q3) - mx)
                      + (-_2bx*q2 + _2bz*q4)*( _2bx*(q2*q3 - q1*q4) + _2bz*(q1*q2 + q3*q4) - my)
                      + (_2bx*q3)*( _2bx*(q1*q3 + q2*q4) + _2bz*(0.5 - q2*q2 - q3*q3) - mz))
            else:
                # 6D varianta (bez mag)
                _2q2 = 2.0*q2; _2q3 = 2.0*q3; _2q4 = 2.0*q4
                _4q2 = 4.0*q2; _4q3 = 4.0*q3
                _8q2 = 8.0*q2; _8q3 = 8.0*q3
                s1 = _4q2*(2.0*(0.5 - q3*q3 - q4*q4) - az) - _2q3*(2.0*(q2*q3 - q1*q4) - ay) + _2q4*(2.0*(q1*q3 + q2*q4) - az)
                s2 = _2q2*(2.0*(q2*q3 - q1*q4) - ay) + _4q3*(2.0*(0.5 - q2*q2 - q3*q3) - az) + _2q1*(2.0*(q1*q2 + q3*q4) - ax)
                s3 = _2q2*(2.0*(q1*q3 + q2*q4) - ax) - _4q2*(2.0*(0.5 - q2*q2 - q3*q3) - az) + _2q1*(2.0*(q2*q3 - q1*q4) - ay)
                s4 = _2q3*(2.0*(q1*q2 + q3*q4) - ax) + _2q2*(2.0*(q1*q3 + q2*q4) - ay)
            # normalizace gradientu
            norm_s = math.sqrt(s1*s1 + s2*s2 + s3*s3 + s4*s4) + 1e-12
            s1 /= norm_s; s2 /= norm_s; s3 /= norm_s; s4 /= norm_s

            # korekce
            qDot1 -= beta * s1
            qDot2 -= beta * s2
            qDot3 -= beta * s3
            qDot4 -= beta * s4

        # --- integrace ---
        q1 += qDot1 * dt
        q2 += qDot2 * dt
        q3 += qDot3 * dt
        q4 += qDot4 * dt
        self.q = _q_normalize(np.array([q1, q2, q3, q4], dtype=np.float32))

        # safety guard
        if not np.isfinite(self.q).all():
            self.q[:] = [1.0, 0.0, 0.0, 0.0]

        return self.q.copy()


class OrientationEstimator(Madgwick9D):
    pass # TODO Ověřit funkčnost