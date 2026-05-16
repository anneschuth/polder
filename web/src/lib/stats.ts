import { loadAll, isMandateCurrent, currentName, personDisplayName, type Person, type Organization, type Post } from './data';

export interface MandatesPerPerson {
  person: Person;
  total: number;
  current: number;
}

export function topMandatesPerPerson(limit = 50): MandatesPerPerson[] {
  const { people } = loadAll();
  return people
    .map((p) => ({
      person: p,
      total: (p.mandaten ?? []).length,
      current: (p.mandaten ?? []).filter(isMandateCurrent).length,
    }))
    .sort((a, b) => b.total - a.total || b.current - a.current)
    .slice(0, limit);
}

export interface BestuurslaagBucket {
  type: string;
  totalOrgs: number;
  currentMandates: number;
  totalMandates: number;
}

export function bestuurslagen(): BestuurslaagBucket[] {
  const { orgs, mandatesByOrg } = loadAll();
  const buckets = new Map<string, BestuurslaagBucket>();
  for (const o of orgs) {
    const b = buckets.get(o.type) ?? { type: o.type, totalOrgs: 0, currentMandates: 0, totalMandates: 0 };
    b.totalOrgs += 1;
    const ms = mandatesByOrg.get(o.id) ?? [];
    for (const { mandate } of ms) {
      b.totalMandates += 1;
      if (isMandateCurrent(mandate)) b.currentMandates += 1;
    }
    buckets.set(o.type, b);
  }
  return [...buckets.values()].sort((a, b) => b.currentMandates - a.currentMandates);
}

export interface YearBucket {
  year: number;
  count: number;
}

export function appointmentsPerYear(): YearBucket[] {
  const { people } = loadAll();
  const counts = new Map<number, number>();
  for (const p of people) {
    for (const m of p.mandaten ?? []) {
      if (!m.start_date) continue;
      const y = Number(m.start_date.slice(0, 4));
      if (Number.isFinite(y)) counts.set(y, (counts.get(y) ?? 0) + 1);
    }
  }
  return [...counts.entries()]
    .map(([year, count]) => ({ year, count }))
    .sort((a, b) => a.year - b.year);
}

export interface SourceBucket {
  id: string;
  records: number;
  /** Most recent `retrieved` date seen for this source (ISO YYYY-MM-DD). */
  latest: string;
}

export function recordsPerSource(): SourceBucket[] {
  const { people, orgs, posts } = loadAll();
  const counts = new Map<string, number>();
  const latest = new Map<string, string>();
  const tally = (sources?: { id: string; retrieved?: string }[]) => {
    for (const s of sources ?? []) {
      counts.set(s.id, (counts.get(s.id) ?? 0) + 1);
      const r = s.retrieved ?? '';
      if (r > (latest.get(s.id) ?? '')) latest.set(s.id, r);
    }
  };
  for (const p of people) tally(p.sources);
  for (const o of orgs) tally(o.sources);
  for (const p of posts) tally(p.sources);
  return [...counts.entries()]
    .map(([id, records]) => ({ id, records, latest: latest.get(id) ?? '' }))
    .sort((a, b) => b.records - a.records);
}

/**
 * Most recent `retrieved` date across all record-level sources, as a real
 * data-freshness signal. Returns ISO YYYY-MM-DD, or '' if no source carries
 * a retrieved date. NOT the build date; this reflects when the data was
 * last refreshed from upstream.
 */
export function lastUpdated(): string {
  let max = '';
  for (const s of recordsPerSource()) {
    if (s.latest > max) max = s.latest;
  }
  return max;
}

export interface FunctionBucket {
  classification: string;
  posts: number;
  currentHolders: number;
}

export function postsByClassification(): FunctionBucket[] {
  const { posts, mandatesByPost } = loadAll();
  const buckets = new Map<string, FunctionBucket>();
  for (const p of posts) {
    const key = p.classification ?? 'overig';
    const b = buckets.get(key) ?? { classification: key, posts: 0, currentHolders: 0 };
    b.posts += 1;
    const ms = mandatesByPost.get(p.id) ?? [];
    b.currentHolders += ms.filter((x) => isMandateCurrent(x.mandate)).length;
    buckets.set(key, b);
  }
  return [...buckets.values()].sort((a, b) => b.posts - a.posts);
}

export interface LabelBucket {
  prefix: string;
  posts: number;
  currentHolders: number;
}

const LABEL_PREFIXES = [
  'Minister',
  'Staatssecretaris',
  'Burgemeester',
  'Wethouder',
  'Raadslid',
  'Statenlid',
  'Gedeputeerde',
  'Commissaris van de Koning',
  'Senator',
  'Tweede Kamerlid',
  'Lid Tweede Kamer',
  'Lid Eerste Kamer',
  'Voorzitter',
  'Secretaris-generaal',
  'Directeur-generaal',
  'Plv. directeur-generaal',
  'Directeur',
  'Afdelingshoofd',
  'Inspecteur-generaal',
  'Procureur-generaal',
  'President',
  'Rechter',
  'Officier van justitie',
];

export function postsByLabelPrefix(): LabelBucket[] {
  const { posts, mandatesByPost } = loadAll();
  const buckets = new Map<string, LabelBucket>();
  for (const prefix of LABEL_PREFIXES) buckets.set(prefix, { prefix, posts: 0, currentHolders: 0 });
  // Labels in the data are mostly lowercase ("wethouder X"), so match
  // case-insensitively against the readable display prefixes.
  const lowered = LABEL_PREFIXES.map((p) => p.toLowerCase());
  for (const p of posts) {
    const label = (p.label ?? '').toLowerCase();
    for (let i = 0; i < lowered.length; i += 1) {
      if (label.startsWith(lowered[i])) {
        const b = buckets.get(LABEL_PREFIXES[i])!;
        b.posts += 1;
        const ms = mandatesByPost.get(p.id) ?? [];
        b.currentHolders += ms.filter((x) => isMandateCurrent(x.mandate)).length;
        break;
      }
    }
  }
  return [...buckets.values()].filter((b) => b.posts > 0).sort((a, b) => b.posts - a.posts);
}

export interface Totals {
  people: number;
  orgs: number;
  posts: number;
  mandates: number;
  currentMandates: number;
}

export function totals(): Totals {
  const { people, orgs, posts } = loadAll();
  let mandates = 0;
  let currentMandates = 0;
  for (const p of people) {
    for (const m of p.mandaten ?? []) {
      mandates += 1;
      if (isMandateCurrent(m)) currentMandates += 1;
    }
  }
  return { people: people.length, orgs: orgs.length, posts: posts.length, mandates, currentMandates };
}
