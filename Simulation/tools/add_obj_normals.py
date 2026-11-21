#!/usr/bin/env python3
"""Add smooth vertex normals to Wavefront OBJ meshes that have none.

Gazebo (gz-common's OBJ loader) does not compute missing normals: a mesh without `vn` lines renders flat white
in Gazebo Harmonic (GUI and camera sensors) — this happened to kmutt_terrain/meshes/core.obj (satellite ground)
and kmutt_trees/meshes/trees.obj from the Unity "KMUTT > Build Map" export. Run again after every re-export:

    python3 Simulation/tools/add_obj_normals.py Simulation/models/*/meshes/*.obj

One normal per vertex (area-weighted average of its faces' normals, same index as the vertex), faces rewritten
`f v/vt` -> `f v/vt/v`, `f v` -> `f v//v`. Files that already have normals are left alone.
"""
import sys


def add_normals(path):
    with open(path) as f:
        lines = f.read().splitlines()
    if any(l.startswith('vn ') for l in lines):
        print(f'{path}: has normals, skipped')
        return
    verts = [(float(p[1]), float(p[2]), float(p[3])) for p in (l.split() for l in lines if l.startswith('v '))]
    normals = [[0.0, 0.0, 0.0] for _ in verts]
    faces = []
    for l in lines:
        if l.startswith('f '):
            idx = [int(t.split('/')[0]) for t in l.split()[1:]]
            idx = [i - 1 if i > 0 else len(verts) + i for i in idx]   # OBJ is 1-based, negative = relative
            faces.append(idx)
            a = verts[idx[0]]
            for k in range(1, len(idx) - 1):   # fan triangulation; cross product length = 2 x area -> weighted
                b, c = verts[idx[k]], verts[idx[k + 1]]
                u = (b[0] - a[0], b[1] - a[1], b[2] - a[2]); v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
                n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
                for i in (idx[0], idx[k], idx[k + 1]):
                    normals[i][0] += n[0]; normals[i][1] += n[1]; normals[i][2] += n[2]
    out = []
    vn_written = False
    for l in lines:
        if l.startswith('f '):
            if not vn_written:   # normals block before the first face
                for n in normals:
                    s = (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5
                    out.append('vn %.4f %.4f %.4f' % ((n[0] / s, n[1] / s, n[2] / s) if s else (0.0, 0.0, 1.0)))
                vn_written = True
            toks = []
            for t in l.split()[1:]:
                parts = t.split('/')
                vi = parts[0]
                vt = parts[1] if len(parts) > 1 else ''
                toks.append(f'{vi}/{vt}/{vi}')
            out.append('f ' + ' '.join(toks))
        else:
            out.append(l)
    with open(path, 'w', newline='\n') as f:
        f.write('\n'.join(out) + '\n')
    print(f'{path}: {len(verts)} normals added, {len(faces)} faces')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for p in sys.argv[1:]:
        add_normals(p)
