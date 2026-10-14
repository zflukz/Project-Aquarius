# PX4 Gazebo Sim: Baylands 3D Terrain Setup Guide

This guide details the complete step-by-step process for configuring, downloading, and running the **Baylands** 3D terrain environment in PX4 SITL with Gazebo Sim, including troubleshooting network asset hangs and process errors.

> **Docker (`px4-sim` container): this is automated.** The image pre-caches Baylands at build time,
> `entrypoint.sh` re-downloads it if the cache is empty, clears stale PX4/Gazebo processes, and
> launches with `PX4_GZ_WORLD=baylands` (the default). The cache lives in the `gz-fuel-cache`
> volume. Set `PX4_GZ_WORLD=default` in `docker/.env` for a lighter world.
> See `docker/README.md`. The manual steps below are for debugging inside the container
> (`docker exec -it px4-sim bash`) or for a native install.

---

## 1. Overview & Make Target

In PX4 SITL using the `x500` drone model, the target syntax to launch the Baylands world is:

```bash
cd /px4
make px4_sitl gz_x500_baylands
```

Alternatively, you can pass the world name via environment variable:

```bash
PX4_GZ_WORLD=baylands make px4_sitl gz_x500
```

---

## 2. Why Baylands Hangs on First Launch

The Baylands environment relies on external 3D heightmaps, ground meshes, and high-resolution textures hosted on Gazebo Fuel (`fuel.gazebosim.org`).

* **The Problem:** On the first launch, Gazebo Sim attempts to download these assets dynamically in the background without displaying a progress bar.
* **The Symptom:** When Docker container network calls or C++ Fuel client requests stall, Gazebo fails to initialize the world transport service, leaving PX4 trapped in a `Waiting for Gazebo world...` loop.

---

## 3. Step-by-Step Setup: Pre-caching Baylands Assets

To avoid simulation hangs, manually pre-cache the Baylands model into local storage using Gazebo's native `gz fuel` CLI tool.

### Step 1: Open Your Docker Container
Attach to your running PX4 container:

```bash
docker exec -it px4-sim bash
```

### Step 2: Download Baylands Model Assets
Run Gazebo's Fuel download command inside the container:

```bash
gz fuel download -u "https://fuel.gazebosim.org/1.0/openrobotics/models/Baylands"
```

### Step 3: Verify the Cached Model Directory
Check that the assets were downloaded and unpacked properly:

```bash
ls -la ~/.gz/fuel/fuel.gazebosim.org/openrobotics/models/baylands/
```

*Expected output:* You should see a numerical version directory (such as `3` or `4`) containing model configuration and mesh files (`model.config`, `model.sdf`, `meshes/`).

---

## 4. Launching the Baylands World

Once the model files are present in the local cache, return to `/px4` and start SITL:

```bash
cd /px4
make px4_sitl gz_x500_baylands
```

Wait until PX4 displays:
```text
INFO  [px4] Ready for takeoff!
```

---

## 5. Troubleshooting Common Errors

### Issue A: `PX4 server already running for instance 0`
* **Cause:** A previous PX4 or Gazebo process was left running in the background.
* **Fix:** Kill all stuck processes before relaunching:
  ```bash
  pkill -9 -f px4 ; pkill -9 -f gz
  ```

### Issue B: `cd: too many arguments`
* **Cause:** Attempting to run `cd` and `make` on the same line without a separator.
* **Fix:** Execute as two separate commands or combine with `&&`:
  ```bash
  cd /px4 && make px4_sitl gz_x500_baylands
  ```

### Issue C: `unzip: cannot find zipfile directory`
* **Cause:** Using raw `curl` on the Fuel API endpoint (`/4/files`) downloads a JSON index instead of a zip file.
* **Fix:** Use `gz fuel download -u "https://fuel.gazebosim.org/1.0/openrobotics/models/Baylands"` instead of manual `curl`.

---

## 6. Quick Verification Checklist

| Step | Command / Check | Expected Result |
| :--- | :--- | :--- |
| **1. Asset Pre-cache** | `gz fuel download -u ...` | Downloads Baylands model into `~/.gz/fuel/` |
| **2. Verify Cache** | `ls -la ~/.gz/fuel/.../baylands/` | Shows version folder (`3` or `4`) |
| **3. Kill Old Processes**| `pkill -9 -f px4 ; pkill -9 -f gz` | Clears stuck background instances |
| **4. Launch Simulation** | `cd /px4 && make px4_sitl gz_x500_baylands` | Gazebo opens with 3D Baylands terrain |
| **5. PX4 Status** | Terminal 1 output | `INFO [px4] Ready for takeoff!` |
