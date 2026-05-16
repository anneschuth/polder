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

export interface Appointment {
  decision?: string;
  kb_nummer?: string;
  staatscourant_url?: string;
  [k: string]: unknown;
}

export interface Mandate {
  id: string;
  organization_id: string;
  post_id?: string | null;
  role?: string;
  start_date?: string | null;
  end_date?: string | null;
  appointment?: Appointment;
  sources?: Source[];
  confidence?: number;
}

export interface PersonName {
  full?: string;
  family?: string;
  given?: string;
  tussenvoegsel?: string;
  initials?: string;
  honorifics_pre?: string[];
  honorifics_post?: string[];
}

export interface PersonIdentifiers {
  tk_persoon_id?: string;
  wikidata?: string;
  abd_id?: string;
  allmanak_id?: string;
  [k: string]: string | undefined;
}

export interface Person {
  id: string;
  slug: string;
  name: PersonName;
  birth?: { year?: number };
  gender?: 'm' | 'f' | 'x' | string;
  mandaten?: Mandate[];
  sources?: Source[];
  identifiers?: PersonIdentifiers;
  [key: string]: unknown;
}

export interface OrgName {
  value: string;
  abbr?: string;
  valid_from?: string;
  valid_until?: string | null;
}

export interface OrgIdentifiers {
  oin?: string;
  tooi?: string;
  owms?: string;
  wikidata?: string;
  roo_id?: string;
  organisatiecode?: string;
  kvk?: string;
  rsin?: string;
  ictu?: string;
  atu?: string;
  btw?: string;
  loonheffing?: string;
  [k: string]: string | undefined;
}

export interface Grondslag {
  opschrift?: string;
  referentie?: string;
}

export interface OrgClassification {
  type: string;
  url?: string;
  value?: string;
  eind_datum?: string;
  wettelijke_grondslagen?: Grondslag[];
}

export interface OrgAddress {
  type?: string;
  openbare_ruimte?: string;
  huisnummer?: string;
  huisnummer_toevoeging?: string;
  postbus?: string;
  postcode?: string;
  woonplaats?: string;
  provincie?: string;
  regio?: string;
  land?: string;
  ter_attentie_van?: string;
  antwoordnummer?: string;
  toelichting?: string;
  [k: string]: unknown;
}

export interface OrgContact {
  website?: string;
  email?: string;
  phone?: string;
  fax?: string;
  bezoekadres?: string;
  postadres?: string;
  beschrijving?: string;
  addresses?: OrgAddress[];
  phones?: { nummer: string; label?: string }[];
  emails?: { email: string; label?: string }[];
  internet_addresses?: { url: string; label?: string }[];
  contact_forms?: { url: string; label?: string }[];
  social_media?: { platform?: string; gebruikersnaam?: string; url?: string }[];
}

export interface OrgGeography {
  oppervlakte?: string;
  oppervlakte_km2?: number;
  aantal_inwoners?: number;
  inwoners?: string;
  inwoners_per_km2?: number;
  bevat_plaatsen?: string[];
}

export interface OrgCouncil {
  total_seats?: number;
  parties?: { naam: string; aantal_zetels: number }[];
}

export interface OrgWoo {
  wooInformatie?: { urls?: { url?: string; overzichtURL?: string } };
  wooIndex?: {
    documentLocatie?: { informatiecategorie?: string; url?: string; toelichting?: string }[];
  };
  wooVerzoek?: { url?: string };
  wooContactpersoon?: string;
}

export interface OrgRef {
  naam?: string;
  roo_id?: string;
  tooi?: string;
  owms?: string;
  org_id?: string;
}

export interface Organization {
  id: string;
  slug: string;
  type: string;
  subtype?: string;
  classification?: string;
  identifiers?: OrgIdentifiers;
  legal_form?: string;
  zbo_kind?: string;
  advisory_kind?: string;
  subname?: string;
  parent_id?: string | null;
  relation_to_ministerie?: OrgRef;
  hoort_bij_gemeenschappelijke_regeling?: OrgRef;
  names: OrgName[];
  description?: { text?: string; url?: string };
  policy_areas?: { naam?: string; tooi?: string }[];
  kaderwet?: unknown;
  wettelijke_grondslagen?: Grondslag[];
  taken_en_bevoegdheden?: unknown;
  evaluations?: {
    datum?: string;
    kamerstuknummer?: string;
    referentie?: string;
    naam_rapport?: string;
  }[];
  doorlichtingen?: unknown[];
  classifications?: OrgClassification[];
  woo?: OrgWoo;
  organogram_url?: string;
  afspraak?: { email?: string; telefoonnummer?: string; url?: string };
  geography?: OrgGeography;
  council?: OrgCouncil;
  gr_meta?: unknown;
  contact?: OrgContact;
  valid_from?: string;
  valid_until?: string | null;
  last_mutation?: string;
  last_verified?: string;
  successor_id?: string[] | string | null;
  predecessor_id?: string[] | string | null;
  sources?: Source[];
  [key: string]: unknown;
}

export interface Post {
  id: string;
  slug: string;
  organization_id: string;
  label: string;
  classification?: string;
  subtype?: string;
  seat_count?: number | null;
  valid_from?: string;
  valid_until?: string | null;
  roo_functie_id?: string;
  roo_naam?: string;
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

export interface Loaded {
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

/** Normalize successor_id/predecessor_id which may be a string, array or null. */
export function asIdArray(v: string[] | string | null | undefined): string[] {
  if (!v) return [];
  return Array.isArray(v) ? v : [v];
}

/** Sibling orgs: same parent_id, excluding self, sorted by name (NL). */
export function siblingOrgs(org: Organization, data: Loaded): Organization[] {
  if (!org.parent_id) return [];
  return (data.childrenByOrg.get(org.parent_id) ?? [])
    .filter((o) => o.id !== org.id)
    .sort((a, b) => currentName(a).localeCompare(currentName(b), 'nl'));
}

export interface PostGroup {
  post: Post;
  entries: { person: Person; mandate: Mandate }[];
}

/** People related to an org via any of its posts, grouped by post (current first). */
export function peopleByPostOfOrg(org: Organization, data: Loaded): PostGroup[] {
  const posts = (data.postsByOrg.get(org.id) ?? [])
    .slice()
    .sort((a, b) => a.label.localeCompare(b.label, 'nl'));
  return posts
    .map((post) => {
      const entries = (data.mandatesByPost.get(post.id) ?? []).slice().sort((a, b) => {
        const ac = isMandateCurrent(a.mandate) ? 0 : 1;
        const bc = isMandateCurrent(b.mandate) ? 0 : 1;
        return ac - bc || (b.mandate.start_date ?? '').localeCompare(a.mandate.start_date ?? '');
      });
      return { post, entries };
    })
    .filter((g) => g.entries.length > 0);
}

/** "Kamerlid 2017–2021, minister sinds 2024" — current first, then up to 3 recent past. */
export function careerSummary(person: Person): string {
  const ms = sortedMandates(person.mandaten ?? []);
  if (ms.length === 0) return '';
  const label = (m: Mandate): string => {
    const role = (m.role ?? '').trim() || 'functie';
    const sy = m.start_date?.slice(0, 4);
    if (isMandateCurrent(m)) return sy ? `${role} sinds ${sy}` : role;
    const ey = m.end_date?.slice(0, 4);
    return sy && ey ? `${role} ${sy}–${ey}` : role;
  };
  const current = ms.filter((m) => isMandateCurrent(m)).map(label);
  const past = ms
    .filter((m) => !isMandateCurrent(m))
    .slice(0, 3)
    .map(label);
  return [...current, ...past].join(', ');
}

/** External profile links for a person. Replaces the broken tk_id/ek_id logic. */
export function personExternalLinks(p: Person): { label: string; url: string }[] {
  const out: { label: string; url: string }[] = [];
  const ids = p.identifiers ?? {};
  if (ids.wikidata) {
    out.push({ label: 'Wikidata', url: `https://www.wikidata.org/wiki/${ids.wikidata}` });
  }
  if (ids.tk_persoon_id) {
    // The UUID has no public profile URL pattern; link the OData source if we
    // have one (person- or mandate-level), else fall back to the TK homepage.
    const fromPerson = (p.sources ?? []).find((s) => s.id === 'tk_odata' && s.url);
    const fromMandate = (p.mandaten ?? [])
      .flatMap((m) => m.sources ?? [])
      .find((s) => s.id === 'tk_odata' && s.url);
    out.push({
      label: 'Tweede Kamer',
      url: fromPerson?.url ?? fromMandate?.url ?? 'https://www.tweedekamer.nl',
    });
  }
  if (ids.allmanak_id) {
    out.push({ label: 'Almanak', url: `https://almanak.overheid.nl/${ids.allmanak_id}/` });
  }
  // Eerste Kamer has no id field; only reachable via the ek_scrape source URL.
  const ekPerson = (p.sources ?? []).find((s) => s.id === 'ek_scrape' && s.url);
  const ekMandate = (p.mandaten ?? [])
    .flatMap((m) => m.sources ?? [])
    .find((s) => s.id === 'ek_scrape' && s.url);
  const ekUrl = ekPerson?.url ?? ekMandate?.url;
  if (ekUrl) out.push({ label: 'Eerste Kamer', url: ekUrl });
  return out;
}

/**
 * Accent color for section headings/icons. The design-system has no
 * per-org-type palette, so we use the single NLDD accent token everywhere
 * rather than inventing colors.
 */
export function orgAccent(_type?: string | undefined): string {
  return 'var(--primitives-color-accent-500)';
}

/**
 * Timeline bar color per post classification — one distinct design-system
 * palette token per kind of office (no invented colors). Each token is a
 * real NLDD palette family with a usable dark shade.
 */
const CLASSIFICATION_COLOR: Record<string, string> = {
  bewindspersoon: 'var(--primitives-color-lintblauw-550)',
  kamerlid: 'var(--primitives-color-hemelblauw-750)',
  statenlid: 'var(--primitives-color-lichtblauw-750)',
  raadslid: 'var(--primitives-color-donkerblauw-750)',
  'commissaris-vd-koning': 'var(--primitives-color-paars-650)',
  gedeputeerde: 'var(--primitives-color-violet-650)',
  wethouder: 'var(--primitives-color-roze-750)',
  burgemeester: 'var(--primitives-color-robijnrood-750)',
  'abd-tmg': 'var(--primitives-color-donkergroen-750)',
  'abd-directeur': 'var(--primitives-color-groen-550)',
  'abd-afdelingshoofd': 'var(--primitives-color-mosgroen-750)',
  'abd-projectleider': 'var(--primitives-color-mintgroen-650)',
  gemeentesecretaris: 'var(--primitives-color-donkerbruin-750)',
  provinciesecretaris: 'var(--primitives-color-bruin-750)',
  griffier: 'var(--primitives-color-donkergeel-550)',
  dijkgraaf: 'var(--primitives-color-groen-550)',
  'db-waterschap': 'var(--primitives-color-mosgroen-750)',
  'ab-waterschap': 'var(--primitives-color-mintgroen-650)',
  'voorzitter-hcs': 'var(--primitives-color-geel-750)',
  'lid-hcs': 'var(--primitives-color-donkergeel-550)',
  'rvb-zbo': 'var(--primitives-color-oranje-550)',
  rechter: 'var(--primitives-color-rood-550)',
  'officier-van-justitie': 'var(--primitives-color-robijnrood-750)',
  gezaghebber: 'var(--primitives-color-oranje-550)',
  overig: 'var(--primitives-color-coolgray-500)',
};

/** Human-readable label per classification, for the timeline legend. */
export const CLASSIFICATION_LABEL: Record<string, string> = {
  bewindspersoon: 'Bewindspersoon',
  kamerlid: 'Kamerlid',
  statenlid: 'Statenlid',
  raadslid: 'Raadslid',
  'commissaris-vd-koning': 'Commissaris van de Koning',
  gedeputeerde: 'Gedeputeerde',
  wethouder: 'Wethouder',
  burgemeester: 'Burgemeester',
  'abd-tmg': 'ABD-topmanagement',
  'abd-directeur': 'ABD-directeur',
  'abd-afdelingshoofd': 'ABD-afdelingshoofd',
  'abd-projectleider': 'ABD-projectleider',
  gemeentesecretaris: 'Gemeentesecretaris',
  provinciesecretaris: 'Provinciesecretaris',
  griffier: 'Griffier',
  dijkgraaf: 'Dijkgraaf',
  'db-waterschap': 'DB waterschap',
  'ab-waterschap': 'AB waterschap',
  'voorzitter-hcs': 'Voorzitter hoog college',
  'lid-hcs': 'Lid hoog college',
  'rvb-zbo': 'Bestuur ZBO',
  rechter: 'Rechter',
  'officier-van-justitie': 'Officier van justitie',
  gezaghebber: 'Gezaghebber',
  overig: 'Overig',
};

export function classificationAccent(classification?: string): string {
  return (
    CLASSIFICATION_COLOR[classification ?? ''] ??
    'var(--primitives-color-coolgray-500)'
  );
}

export function classificationLabel(classification?: string): string {
  return CLASSIFICATION_LABEL[classification ?? ''] ?? 'Overig';
}
