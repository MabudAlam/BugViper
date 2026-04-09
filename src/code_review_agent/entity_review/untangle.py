from .types import ChangeGroup, EntityReview


class UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, a: int, b: int) -> None:
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            self.parent[ra] = rb
        elif self.rank[ra] > self.rank[rb]:
            self.parent[rb] = ra
        else:
            self.parent[rb] = ra
            self.rank[ra] += 1


def untangle(
    reviews: list[EntityReview], dependency_edges: list[tuple[str, str]]
) -> list[ChangeGroup]:
    if not reviews:
        return []

    id_to_idx: dict[str, int] = {r.entity_id: i for i, r in enumerate(reviews)}

    uf = UnionFind(len(reviews))

    for src, dst in dependency_edges:
        if src in id_to_idx and dst in id_to_idx:
            uf.union(id_to_idx[src], id_to_idx[dst])

    groups_map: dict[int, list[int]] = {}
    for i in range(len(reviews)):
        root = uf.find(i)
        if root not in groups_map:
            groups_map[root] = []
        groups_map[root].append(i)

    groups: list[ChangeGroup] = []
    for indices in groups_map.values():
        entity_ids = [reviews[i].entity_id for i in indices]

        if len(indices) == 1:
            label = reviews[indices[0]].entity_name
        else:
            files = [reviews[i].file_path for i in indices]
            common = _common_prefix(files)
            label = common if common else f"{len(entity_ids)} entities"

        groups.append(ChangeGroup(id=0, label=label, entity_ids=entity_ids))

    groups.sort(key=lambda g: len(g.entity_ids), reverse=True)

    for i, group in enumerate(groups):
        group.id = i

    return groups


def _common_prefix(strings: list[str]) -> str:
    if not strings:
        return ""

    first = strings[0]
    length = len(first)

    for s in strings[1:]:
        length = min(length, len(s))
        for i, (a, b) in enumerate(zip(first.encode(), s.encode())):
            if a != b:
                length = min(length, i)
                break

    prefix = first[:length]
    if "/" in prefix:
        return prefix[: prefix.rfind("/") + 1]
    return prefix
