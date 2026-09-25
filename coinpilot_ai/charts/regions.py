"""在直线排列中查找点击位置所在的有界面；仅在用户请求填色时计算。"""
import math


def enclosed_region(lines, point):
    """lines: (起点, 方向终点, 参数下界, 参数上界)，支持线段、射线、无限线。"""
    eps = 1e-7
    def sub(a, b):
        return (a[0]-b[0], a[1]-b[1])
    def cross(a, b):
        return a[0]*b[1]-a[1]*b[0]
    segments = []
    for a, b, low, high in lines:
        d = sub(b, a)
        if math.hypot(*d) > eps:
            segments.append((a, d, low, high, [t for t in (low, high) if math.isfinite(t)]))
    for i, (a, d, low, high, cuts) in enumerate(segments):
        for b, e, lo, hi, other in segments[i+1:]:
            delta, det = sub(b, a), cross(d, e)
            if abs(det) > eps*math.hypot(*d)*math.hypot(*e):
                t, u = cross(delta, e)/det, cross(delta, d)/det
                if low-eps <= t <= high+eps and lo-eps <= u <= hi+eps:
                    cuts.append(t)
                    other.append(u)
            elif abs(cross(delta, d)) <= eps*math.hypot(*d):
                # 重合线在彼此端点处分割，避免重复边或遗漏交点。
                for origin, direction, bounds, dest, start, vector, limits in (
                    (b, e, (lo, hi), cuts, a, d, (low, high)),
                    (a, d, (low, high), other, b, e, (lo, hi))):
                    length = vector[0]**2+vector[1]**2
                    for v in bounds:
                        if math.isfinite(v):
                            q = sub((origin[0]+v*direction[0], origin[1]+v*direction[1]), start)
                            t = (q[0]*vector[0]+q[1]*vector[1])/length
                            if limits[0]-eps <= t <= limits[1]+eps:
                                dest.append(t)
    nodes, graph = [], {}
    identities = {}
    def node(p):
        key = (round(p[0], 6), round(p[1], 6))
        if key not in identities:
            identities[key] = len(nodes)
            nodes.append(p)
        return identities[key]
    for a, d, _, _, cuts in segments:
        ids = [node((a[0]+t*d[0], a[1]+t*d[1])) for t in sorted(set(cuts))]
        for u, v in zip(ids, ids[1:]):
            if u != v:
                graph.setdefault(u, set()).add(v)
                graph.setdefault(v, set()).add(u)
    ordered = {u: sorted(vs, key=lambda v: math.atan2(nodes[v][1]-nodes[u][1], nodes[v][0]-nodes[u][0]))
               for u, vs in graph.items()}
    visited, candidates = set(), []
    for u, vs in ordered.items():
        for v in vs:
            if (u, v) in visited:
                continue
            face, edge = [], (u, v)
            while edge not in visited:
                visited.add(edge)
                a, b = edge
                face.append(nodes[a])
                neighbors = ordered[b]
                edge = (b, neighbors[(neighbors.index(a)-1) % len(neighbors)])
            if edge != (u, v) or len(face) < 3:
                continue
            area = sum(cross(a, b) for a, b in zip(face, face[1:]+face[:1]))/2
            if area <= eps:
                continue  # 无界外部面及退化边不产生填充。
            x, y = point
            inside = False
            for a, b in zip(face, face[1:]+face[:1]):
                if (a[1] > y) != (b[1] > y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
                    inside = not inside
            if inside:
                candidates.append((area, face))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None
