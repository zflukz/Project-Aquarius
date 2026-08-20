"""
3D Drone Swarm Simulation - Base Code
--------------------------------------
Boids-style flocking (separation, alignment, cohesion) + goal-seeking
+ boundary containment, in 3D, animated with matplotlib.

This is meant as a STARTING POINT. Extend it with:
  - collision avoidance vs static obstacles
  - realistic drone dynamics (thrust/torque limits, max accel)
  - communication topology / limited sensing range
  - leader-follower or formation shapes
  - task allocation / search coverage

Run:
    python drone_swarm_3d.py
"""

import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401 (registers 3D projection)
from matplotlib.animation import FuncAnimation

# ----------------------------
# Config
# ----------------------------
N_DRONES = 30
DIM = 3
BOUNDS = np.array([100.0, 100.0, 60.0])   # world size in x, y, z
DT = 0.1                                   # simulation timestep (s)

MAX_SPEED = 12.0
MAX_ACCEL = 8.0

# Behavior weights - tune these to change swarm character
W_SEPARATION = 1.6
W_ALIGNMENT = 1.0
W_COHESION = 1.0
W_GOAL = 0.8
W_BOUNDARY = 3.0

SEPARATION_RADIUS = 6.0     # drones try to stay this far apart
NEIGHBOR_RADIUS = 20.0      # "sensing range" for alignment/cohesion
BOUNDARY_MARGIN = 10.0      # start avoiding walls this far from edge

GOAL = np.array([80.0, 80.0, 45.0])  # target point the swarm moves toward


class DroneSwarm:
    def __init__(self, n=N_DRONES, bounds=BOUNDS):
        self.n = n
        self.bounds = bounds

        # random initial positions in a cluster near one corner
        self.pos = np.random.uniform(
            low=[5, 5, 5], high=[25, 25, 25], size=(n, DIM)
        )
        # small random initial velocities
        self.vel = np.random.uniform(-2, 2, size=(n, DIM))

    def step(self, dt=DT, goal=GOAL):
        accel = np.zeros((self.n, DIM))

        for i in range(self.n):
            pos_i = self.pos[i]
            vel_i = self.vel[i]

            # vector to all other drones
            diffs = self.pos - pos_i
            dists = np.linalg.norm(diffs, axis=1)
            dists[i] = np.inf  # ignore self

            neighbor_mask = dists < NEIGHBOR_RADIUS
            close_mask = dists < SEPARATION_RADIUS

            # --- Separation: push away from very close drones ---
            sep_force = np.zeros(DIM)
            if np.any(close_mask):
                push = -diffs[close_mask] / (dists[close_mask, None] ** 2)
                sep_force = push.sum(axis=0)

            # --- Alignment: match average heading of neighbors ---
            align_force = np.zeros(DIM)
            if np.any(neighbor_mask):
                avg_vel = self.vel[neighbor_mask].mean(axis=0)
                align_force = avg_vel - vel_i

            # --- Cohesion: move toward center of mass of neighbors ---
            coh_force = np.zeros(DIM)
            if np.any(neighbor_mask):
                center = self.pos[neighbor_mask].mean(axis=0)
                coh_force = center - pos_i

            # --- Goal seeking ---
            goal_force = goal - pos_i
            goal_norm = np.linalg.norm(goal_force)
            if goal_norm > 1e-6:
                goal_force = goal_force / goal_norm

            # --- Boundary containment (soft push back inside) ---
            bound_force = np.zeros(DIM)
            for d in range(DIM):
                if pos_i[d] < BOUNDARY_MARGIN:
                    bound_force[d] += (BOUNDARY_MARGIN - pos_i[d])
                elif pos_i[d] > self.bounds[d] - BOUNDARY_MARGIN:
                    bound_force[d] -= (pos_i[d] - (self.bounds[d] - BOUNDARY_MARGIN))

            total = (
                W_SEPARATION * sep_force
                + W_ALIGNMENT * align_force
                + W_COHESION * coh_force
                + W_GOAL * goal_force
                + W_BOUNDARY * bound_force
            )

            # clamp acceleration
            norm = np.linalg.norm(total)
            if norm > MAX_ACCEL:
                total = total / norm * MAX_ACCEL

            accel[i] = total

        # integrate
        self.vel += accel * dt
        speeds = np.linalg.norm(self.vel, axis=1)
        over = speeds > MAX_SPEED
        if np.any(over):
            self.vel[over] = (self.vel[over].T / speeds[over] * MAX_SPEED).T

        self.pos += self.vel * dt

        # hard clamp to world bounds (safety net)
        self.pos = np.clip(self.pos, 0, self.bounds)


def main():
    swarm = DroneSwarm()

    fig = plt.figure(figsize=(9, 8))
    ax = fig.add_subplot(111, projection="3d")
    ax.set_xlim(0, BOUNDS[0])
    ax.set_ylim(0, BOUNDS[1])
    ax.set_zlim(0, BOUNDS[2])
    ax.set_xlabel("X (m)")
    ax.set_ylabel("Y (m)")
    ax.set_zlabel("Z (m)")
    ax.set_title("3D Drone Swarm Simulation")

    scat = ax.scatter(
        swarm.pos[:, 0], swarm.pos[:, 1], swarm.pos[:, 2],
        c="royalblue", s=25, depthshade=True,
    )
    goal_marker = ax.scatter(
        [GOAL[0]], [GOAL[1]], [GOAL[2]],
        c="red", marker="*", s=200, label="Goal"
    )
    ax.legend(loc="upper left")

    def update(frame):
        swarm.step()
        scat._offsets3d = (swarm.pos[:, 0], swarm.pos[:, 1], swarm.pos[:, 2])
        return scat,

    anim = FuncAnimation(fig, update, frames=600, interval=30, blit=False)
    plt.tight_layout()
    plt.show()

    return anim  # keep a reference so it isn't garbage collected


if __name__ == "__main__":
    _anim = main()