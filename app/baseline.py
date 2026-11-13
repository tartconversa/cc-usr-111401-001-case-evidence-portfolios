"""现有业务摘要，不推断未观察到的业务关系。"""
def analyze(snapshot):
    return search(snapshot, ['工坊'])

def search(snapshot, terms):
    terms = set(terms)
    found = []
    for row in snapshot.rows('documents'):
        hits = [(p['id'], len(terms.intersection(p['tokens']))) for p in row['paragraphs']]
        score = sum(n for _, n in hits)
        if score:
            found.append({'document': row['id'], 'village': row['village'], 'score': score,
                          'paragraphs': [key for key, n in hits if n]})
    return sorted(found, key=lambda x: (-x['score'], x['document']))
