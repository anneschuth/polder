import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, dirname, basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import yaml from 'js-yaml';

const HERE = dirname(fileURLToPath(import.meta.url));
const DATA_ROOT = join(HERE, '..', '..', '..', 'data');

export interface Source {
  id: string;
  url?: string;
  retrieved?: string;
  fields?: string[];
}

export interface Mandate {
  id: string;
  organization_id: string;
  post_id?: string | null;
  role?: string;
  start_date?: string | null;
  end_date?: string | null;
  appointment?: Record<string, unknown>;
  sources?: Source[];
}

export interface Person {
  id: string;
  slug: string;
  name: {
    full?: string;
    family?: string;
    given?: string;
    initials?: string;
    tussenvoegsel?: string;
    honorifics_pre?: string[];
    honorifics_post?: string[];
  };
  birth?: { year?: number };
  gender?: string;
  mandaten?: Mandate[];
  sources?: Source[];
  identifiers?: Record<string, string>;
  [key: string]: unknown;
}

export interface OrgName {
  value: string;
  abbr?: string;
  valid_from?: string;
  valid_until?: string | null;
}

export interface Organization {
  id: string;
  slug: string;
  type: string;
  classification?: string;
  parent_id?: string | null;
  names: OrgName[];
  identifiers?: Record<string, string>;
  contact?: { website?: string; email?: string; phone?: string };
  valid_from?: string;
  valid_until?: string | null;
  sources?: Source[];
  predecessor_id?: string[];
  successor_id?: string[];
  [key: string]: unknown;
}

export interface Post {
  id: string;
  slug: string;
  organization_id: string;
  label: string;
  classification?: string;
  valid_from?: string;
  valid_until?: string | null;
  sources?: Source[];
  [key: string]: unknown;
}

function walkYaml(root: string): string[] {
  const out: string[] = [];
  function walk(dir: string) {
    for (const entry of readdirSync(dir)) {
      const p = join(dir, entry);
      const st = statSync(p);
      if (st.isDirectory()) walk(p);
      else if (entry.endsWith('.yaml')) out.push(p);
    }
  }
  walk(root);
  return out;
}

function loadYaml<T>(path: string): T {
  return yaml.load(readFileSync(path, 'utf8')) as T;
}

function slugFromId(id: string): string {
  const idx = id.indexOf(':');
  return idx < 0 ? id : id.slice(idx + 1);
}

interface Loaded {
  people: Person[];
  orgs: Organization[];
  posts: Post[];
  personById: Map<string, Person>;
  orgById: Map<string, Organization>;
  postById: Map<string, Post>;
  mandatesByOrg: Map<string, { person: Person; mandate: Mandate }[]>;
  mandatesByPost: Map<string, { person: Person; mandate: Mandate }[]>;
  childrenByOrg: Map<string, Organization[]>;
  postsByOrg: Map<string, Post[]>;
  orgTypes: Set<string>;
}

let cached: Loaded | null = null;

export function loadAll(): Loaded {
  if (cached) return cached;

  const peopleFiles = walkYaml(join(DATA_ROOT, 'personen'));
  const orgFiles = walkYaml(join(DATA_ROOT, 'organisaties'));
  const postFiles = walkYaml(join(DATA_ROOT, 'posten'));

  const people: Person[] = peopleFiles.map((f) => {
    const p = loadYaml<Person>(f);
    p.slug = slugFromId(p.id);
    return p;
  });

  const orgs: Organization[] = orgFiles.map((f) => {
    const o = loadYaml<Organization>(f);
    o.slug = slugFromId(o.id);
    if (!o.type) {
      const rel = relative(join(DATA_ROOT, 'organisaties'), f);
      o.type = rel.split('/')[0] ?? 'overig';
    }
    return o;
  });

  const posts: Post[] = postFiles.map((f) => {
    const p = loadYaml<Post>(f);
    p.slug = slugFromId(p.id);
    return p;
  });

  const personById = new Map(people.map((p) => [p.id, p]));
  const orgById = new Map(orgs.map((o) => [o.id, o]));
  const postById = new Map(posts.map((p) => [p.id, p]));

  const mandatesByOrg = new Map<string, { person: Person; mandate: Mandate }[]>();
  const mandatesByPost = new Map<string, { person: Person; mandate: Mandate }[]>();

  for (const person of people) {
    for (const m of person.mandaten ?? []) {
      if (m.organization_id) {
        const arr = mandatesByOrg.get(m.organization_id) ?? [];
        arr.push({ person, mandate: m });
        mandatesByOrg.set(m.organization_id, arr);
      }
      if (m.post_id) {
        const arr = mandatesByPost.get(m.post_id) ?? [];
        arr.push({ person, mandate: m });
        mandatesByPost.set(m.post_id, arr);
      }
    }
  }

  const childrenByOrg = new Map<string, Organization[]>();
  for (const o of orgs) {
    if (o.parent_id) {
      const arr = childrenByOrg.get(o.parent_id) ?? [];
      arr.push(o);
      childrenByOrg.set(o.parent_id, arr);
    }
  }

  const postsByOrg = new Map<string, Post[]>();
  for (const p of posts) {
    if (p.organization_id) {
      const arr = postsByOrg.get(p.organization_id) ?? [];
      arr.push(p);
      postsByOrg.set(p.organization_id, arr);
    }
  }

  const orgTypes = new Set(orgs.map((o) => o.type));

  cached = {
    people,
    orgs,
    posts,
    personById,
    orgById,
    postById,
    mandatesByOrg,
    mandatesByPost,
    childrenByOrg,
    postsByOrg,
    orgTypes,
  };
  return cached;
}

export function personUrl(slug: string, base = ''): string {
  return `${base}/personen/${slug}/`;
}

export function orgUrl(org: Organization, base = ''): string {
  return `${base}/organisaties/${org.type}/${org.slug}/`;
}

export function postUrl(slug: string, base = ''): string {
  return `${base}/posten/${slug}/`;
}

export function currentName(org: Organization): string {
  const names = org.names ?? [];
  const current = names.find((n) => !n.valid_until) ?? names[names.length - 1];
  return current?.value ?? org.slug;
}

export function currentAbbr(org: Organization): string | undefined {
  const names = org.names ?? [];
  const current = names.find((n) => !n.valid_until) ?? names[names.length - 1];
  return current?.abbr;
}

export function isMandateCurrent(m: Mandate): boolean {
  return !m.end_date;
}

export function sortedMandates(mandaten: Mandate[]): Mandate[] {
  return [...mandaten].sort((a, b) => {
    const ac = isMandateCurrent(a) ? 0 : 1;
    const bc = isMandateCurrent(b) ? 0 : 1;
    if (ac !== bc) return ac - bc;
    const ad = a.start_date ?? '';
    const bd = b.start_date ?? '';
    return bd.localeCompare(ad);
  });
}

export function personDisplayName(p: Person): string {
  if (p.name?.full) return p.name.full;
  const joined = [p.name?.given, p.name?.tussenvoegsel, p.name?.family].filter(Boolean).join(' ');
  return joined || p.slug;
}
