'use client';

import { useState, useEffect, useRef, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import SearchResult from '@/components/SearchResult';
import { MagnifyingGlassIcon } from '@heroicons/react/24/outline';
import { getApiUrl } from '@/lib/api';
import {
  fetchActs,
  fetchRules,
  fetchNotifications,
  fetchCirculars,
  searchNotifications,
  searchCirculars,
  resolveLibraryDoc,
  ActListItem,
  RuleListItem,
  NotificationListItem,
  CircularListItem,
  NotificationDetail,
  CircularDetail,
  getLibraryPdfUrl,
} from '@/lib/library';

// ── Act HTML processing ───────────────────────────────────────────────────────


function processActHtml(html: string, sectionNo: string, matchedSubSection?: string): string {
  const [prefix, num] = sectionNo.split(/\s+/, 2);
  // Allow HTML tags between prefix and number (source HTML often has <span>&nbsp;</span> between them)
  const headingRe = new RegExp(`<p[^>]*>[\\s\\S]*?${prefix}[\\s\\S]{0,300}?${num}[\\s\\S]*?<\\/p>`);

  // Extract the sub-section marker to highlight, e.g. "(5)" from "Section 17(5)"
  let highlightMarker: string | null = null;
  if (matchedSubSection && matchedSubSection !== sectionNo) {
    const m = matchedSubSection.match(/(\([^)]+\))$/);
    if (m) highlightMarker = m[1];
  }

  // Strip typography properties that vary across sections (causing inconsistency)
  // but keep color (blue annotations) and structural props (padding, border).
  const STRIP_PROPS = /\b(font-family|font-size|text-align|letter-spacing|word-spacing|text-transform|font-variant[^:]*|white-space|orphans|widows|text-indent|text-decoration[^:]*|-webkit-[^:]*|background-color|box-sizing|font-style|line-height)\s*:[^;]+;?\s*/gi;

  function normalizeStyle(style: string): string {
    return style.replace(STRIP_PROPS, '').trim().replace(/;+\s*$/, '');
  }

  let out = html
    .replace(headingRe, '')
    .replace(/\s_ngcontent-[^=\s>]*="[^"]*"/g, '')
    // Normalise footnotes div — always apply blue + separator regardless of source style
    .replace(/<div\s[^>]*class="footnotes"[^>]*>/g,
      '<div style="border-top:1px solid #cbd5e1;margin-top:1.5rem;padding-top:0.75rem;color:#2563eb;">'
    )
    .replace(/\sstyle="([^"]*)"/g, (_m, s) => {
      const cleaned = normalizeStyle(s);
      return cleaned ? ` style="${cleaned}"` : '';
    });

  // Fallback indentation: for acts whose source HTML lacks margin/padding on nested items,
  // infer indent level from the list marker pattern and add padding-left.
  // Only applied when no padding-left or margin-left is already present on the <p>.
  const INDENT_MARKERS: Array<[RegExp, string]> = [
    // Deep: roman numerals (i), (ii), (iii), (iv), (v), (vi), (vii), (viii), (ix), (x) ...
    [/^\(x{0,3}(?:ix|iv|v?i{0,3})\)/i, 'padding-left:3rem'],
    // Mid: single letters (a), (b) ... (z)
    [/^\([a-z]\)/, 'padding-left:1.5rem'],
    // Explanations / provisos
    [/^(?:Explanation|Proviso)\s*[—\-:]/, 'padding-left:1.5rem'],
  ];

  out = out.replace(/<p([^>]*)>([\s\S]*?)<\/p>/g, (match, attrs, inner) => {
    // Already has indent style? Leave it alone.
    if (/padding-left|margin-left/i.test(attrs)) return match;
    // Strip tags AND decode common HTML entities so marker patterns match reliably
    const text = inner
      .replace(/<[^>]+>/g, '')
      .replace(/&nbsp;/g, ' ')
      .replace(/&[a-z]+;|&#\d+;/g, ' ')
      .trim();
    for (const [re, indent] of INDENT_MARKERS) {
      if (re.test(text)) {
        // Merge with existing style if any
        const existingStyleMatch = attrs.match(/\sstyle="([^"]*)"/);
        if (existingStyleMatch) {
          const newAttrs = attrs.replace(/\sstyle="[^"]*"/, ` style="${existingStyleMatch[1]};${indent}"`);
          return `<p${newAttrs}>${inner}</p>`;
        }
        return `<p${attrs} style="${indent}">${inner}</p>`;
      }
    }
    return match;
  });

  // Highlight the matched sub-section if coming from search
  if (highlightMarker) {
    out = out.replace(/<p([^>]*)>([\s\S]*?)<\/p>/g, (match, attrs, inner) => {
      const text = inner.replace(/<[^>]+>/g, '').trim();
      if (!text.startsWith(highlightMarker!)) return match;
      return `<p${attrs} id="act-match-target" style="background-color:#fef3c7;border-left:3px solid #f59e0b;padding-left:0.75rem;border-radius:0 4px 4px 0">${inner}</p>`;
    });
  }

  return out;
}

// ── Acts (static JSON via backend) ─────────────────────────────────────────────

interface ActSearchHit {
  act_id: number;
  primary_id: number;
  act_name: string;
  chapter_no: string | null;
  chapter_name: string | null;
  section_no: string;
  section_name: string | null;
  matched_sub_section: string;
  snippet: string | null;
  rrf_score: number;
}

interface StaticAct { key: string; name: string }
interface ActSection {
  idx: number;
  chapter_no: string | null;
  chapter_name: string | null;
  section_no: string;
  section_name: string | null;
}
interface ActSectionFull extends ActSection {
  act_name: string;
  content: string | null;
  html: string | null;
  html_content?: string | null;  // DB endpoint uses this name; normalised to html on load
  matchedSubSection?: string;
}

// ── Rules ──────────────────────────────────────────────────────────────────────

interface RuleSearchHit {
  rule_id: number;
  primary_id: number;
  rule_name: string;
  section_no: string;
  section_name: string | null;
  matched_sub_section: string;
  snippet: string | null;
  rrf_score: number;
}

interface RuleSectionFull {
  primary_id: number;
  act_name: string;
  section_no: string;
  section_name: string | null;
  content: string | null;
  html_content: string | null;
  matchedSubSection?: string;
}

// ── Content types ──────────────────────────────────────────────────────────────

type ContentType = 'case_laws' | 'acts' | 'rules' | 'notifications' | 'circulars';

const CONTENT_TYPES: { id: ContentType; label: string }[] = [
  { id: 'case_laws', label: 'Case Laws' },
  { id: 'acts', label: 'Acts' },
  { id: 'rules', label: 'Rules' },
  { id: 'notifications', label: 'Notifications' },
  { id: 'circulars', label: 'Circulars' },
];

const COURTS = [
  'All courts',
  'Supreme Court of India',
  'Allahabad High Court',
  'Bombay High Court',
  'Calcutta High Court',
  'Gauhati High Court',
  'High Court for State of Telangana',
  'High Court of Andhra Pradesh',
  'High Court of Chhattisgarh',
  'High Court of Delhi',
  'High Court of Gujarat',
  'High Court of Himachal Pradesh',
  'High Court of Jammu and Kashmir',
  'High Court of Jharkhand',
  'High Court of Karnataka',
  'High Court of Kerala',
  'High Court of Madhya Pradesh',
  'High Court of Manipur',
  'High Court of Meghalaya',
  'High Court of Punjab and Haryana',
  'High Court of Rajasthan',
  'High Court of Sikkim',
  'High Court of Tripura',
  'High Court of Uttarakhand',
  'Madras High Court',
  'Orissa High Court',
  'Patna High Court',
];
const IN_FAVOUR = ['All', 'In favour of Assessee', 'In favour of Revenue', 'Partly in favour of Assessee', 'Not Available'];
const ACT_PLACEHOLDER = 'All Acts';

// ── Secondary panel list item ─────────────────────────────────────────────────

type SecondaryItem = {
  primary_id: number;
  label: string;
  sublabel?: string;
  href: string;
};

function buildSecondaryItems(
  type: ContentType,
  acts: ActListItem[],
  rules: RuleListItem[],
  notifications: NotificationListItem[],
  circulars: CircularListItem[],
): SecondaryItem[] {
  if (type === 'acts') {
    return acts.map((a) => ({
      primary_id: a.primary_id,
      label: a.section_no,
      sublabel: a.section_name ?? undefined,
      href: `/library/acts/${a.primary_id}`,
    }));
  }
  if (type === 'rules') {
    return rules.map((r) => ({
      primary_id: r.primary_id,
      label: r.section_no,
      sublabel: r.section_name ?? undefined,
      href: `/library/rules/${r.primary_id}`,
    }));
  }
  if (type === 'notifications') {
    return notifications.map((n) => ({
      primary_id: n.primary_id,
      label: `Notification ${n.notification_no}`,
      sublabel: n.title ?? undefined,
      href: `/library/notifications/${n.primary_id}`,
    }));
  }
  if (type === 'circulars') {
    return circulars.map((c) => ({
      primary_id: c.primary_id,
      label: `Circular ${c.circular_no}`,
      sublabel: c.subject ?? undefined,
      href: `/library/circulars/${c.primary_id}`,
    }));
  }
  return [];
}

const SECONDARY_HEADER: Record<ContentType, string> = {
  case_laws: '',
  acts: 'Sections',
  rules: 'Rules',
  notifications: 'Notifications',
  circulars: 'Circulars',
};

// ── Main page ─────────────────────────────────────────────────────────────────

function SearchPageContent() {
  const searchParams = useSearchParams();
  const router = useRouter();

  const [query, setQuery] = useState(searchParams.get('q') || '');
  const [results, setResults] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeType, setActiveType] = useState<ContentType>('case_laws');
  const [court, setCourt] = useState('');
  const [inFavour, setInFavour] = useState('');
  const [actFilter, setActFilter] = useState('');
  const [sectionFilter, setSectionFilter] = useState('');
  const [availableSections, setAvailableSections] = useState<string[]>([]);
  const [petitioner, setPetitioner] = useState('');
  const [respondent, setRespondent] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [page, setPage] = useState(1);
  const [hasSearched, setHasSearched] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const PAGE_SIZE = 10;
  const didMount = useRef(false);

  // Party dropdown options — populated from /search/parties
  const [availablePetitioners, setAvailablePetitioners] = useState<string[]>([]);
  const [availableRespondents, setAvailableRespondents] = useState<string[]>([]);

  // Act filter options — populated once from /search/acts
  const [availableActs, setAvailableActs] = useState<string[]>([]);

  // Library lists
  const [acts, setActs] = useState<ActListItem[]>([]);
  const [rules, setRules] = useState<RuleListItem[]>([]);
  const [notifications, setNotifications] = useState<NotificationListItem[]>([]);
  const [circulars, setCirculars] = useState<CircularListItem[]>([]);
  const [listsLoaded, setListsLoaded] = useState(false);

  // Static Acts (from JSON files)
  const [staticActList, setStaticActList] = useState<StaticAct[]>([]);
  const [selectedActKey, setSelectedActKey] = useState<string>('');
  const [actSections, setActSections] = useState<ActSection[]>([]);
  const [actSectionsLoading, setActSectionsLoading] = useState(false);
  const [selectedSection, setSelectedSection] = useState<ActSectionFull | null>(null);
  const [sectionLoading, setSectionLoading] = useState(false);

  // Act search
  const [actSearchResults, setActSearchResults] = useState<ActSearchHit[]>([]);
  const [actSearchLoading, setActSearchLoading] = useState(false);
  const [actSearchQuery, setActSearchQuery] = useState('');

  // Notifications / Circulars
  const [selectedNotification, setSelectedNotification] = useState<NotificationListItem | null>(null);
  const [selectedCircular, setSelectedCircular] = useState<CircularListItem | null>(null);
  const [notificationDetail, setNotificationDetail] = useState<NotificationDetail | null>(null);
  const [circularDetail, setCircularDetail] = useState<CircularDetail | null>(null);
  const [notifYear, setNotifYear] = useState<number | null>(null);
  const [notifCategory, setNotifCategory] = useState<string | null>(null);
  const [notifActiveOnly, setNotifActiveOnly] = useState(true);
  const [notifSearchResults, setNotifSearchResults] = useState<NotificationListItem[] | null>(null);
  const [notifSearchLoading, setNotifSearchLoading] = useState(false);
  const [circularYear, setCircularYear] = useState<number | null>(null);
  const [circularCategory, setCircularCategory] = useState<string | null>(null);
  const [circularActiveOnly, setCircularActiveOnly] = useState(true);
  const [circularSearchResults, setCircularSearchResults] = useState<CircularListItem[] | null>(null);
  const [circularSearchLoading, setCircularSearchLoading] = useState(false);

  // Rules
  const [selectedRuleName, setSelectedRuleName] = useState<string>('');
  const [selectedRuleSection, setSelectedRuleSection] = useState<RuleSectionFull | null>(null);
  const [ruleSectionLoading, setRuleSectionLoading] = useState(false);
  const [ruleSearchResults, setRuleSearchResults] = useState<RuleSearchHit[]>([]);
  const [ruleSearchLoading, setRuleSearchLoading] = useState(false);
  const [ruleSearchQuery, setRuleSearchQuery] = useState('');

  // Scroll to highlighted sub-section when a section opens from search
  useEffect(() => {
    if (!selectedSection?.matchedSubSection) return;
    const el = document.getElementById('act-match-target');
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [selectedSection]);

  useEffect(() => {
    if (!selectedNotification) { setNotificationDetail(null); return; }
    resolveLibraryDoc('notification', selectedNotification.primary_id)
      .then((doc) => { if (doc.type === 'notification') setNotificationDetail(doc.document); })
      .catch(() => {});
  }, [selectedNotification]);

  useEffect(() => {
    if (!selectedCircular) { setCircularDetail(null); return; }
    resolveLibraryDoc('circular', selectedCircular.primary_id)
      .then((doc) => { if (doc.type === 'circular') setCircularDetail(doc.document); })
      .catch(() => {});
  }, [selectedCircular]);

  useEffect(() => {
    if (!selectedRuleSection?.matchedSubSection) return;
    const el = document.getElementById('rule-match-target');
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [selectedRuleSection]);

  // Load library lists + act filter options once
  useEffect(() => {
    if (listsLoaded) return;
    setListsLoaded(true);
    fetchActs({ limit: 500 }).then(setActs).catch(() => {});
    fetchRules({ limit: 500 }).then((r) => { setRules(r); if (r.length > 0 && !selectedRuleName) setSelectedRuleName(r[0].act_name); }).catch(() => {});
    fetchNotifications({ limit: 1500 }).then(setNotifications).catch(() => {});
    fetchCirculars({ limit: 500 }).then(setCirculars).catch(() => {});
    // Fetch available Acts for the filter dropdown
    fetch(`${getApiUrl()}/search/acts`)
      .then((r) => r.ok ? r.json() : [])
      .then((acts: string[]) => setAvailableActs(acts))
      .catch(() => {});
    // Fetch static act list
    fetch(`${getApiUrl()}/acts/list`)
      .then((r) => r.ok ? r.json() : [])
      .then((list: StaticAct[]) => {
        setStaticActList(list);
        if (list.length > 0) setSelectedActKey(list[0].key);
      })
      .catch(() => {});
  }, [listsLoaded]);

  // Load sections when selected act changes
  useEffect(() => {
    if (!selectedActKey) return;
    setActSectionsLoading(true);
    setActSections([]);
    setSelectedSection(null);
    setActSearchQuery('');
    setActSearchResults([]);
    fetch(`${getApiUrl()}/acts/${selectedActKey}/sections`)
      .then((r) => r.ok ? r.json() : [])
      .then((sections: ActSection[]) => setActSections(sections))
      .catch(() => {})
      .finally(() => setActSectionsLoading(false));
  }, [selectedActKey]);

  const loadSection = (idx: number) => {
    setSectionLoading(true);
    fetch(`${getApiUrl()}/acts/${selectedActKey}/section/${idx}`)
      .then((r) => r.ok ? r.json() : null)
      .then((s: ActSectionFull | null) => setSelectedSection(s))
      .catch(() => {})
      .finally(() => setSectionLoading(false));
  };

  const buildBaseParams = (q: string, filters: { court: string; inFavour: string }) => {
    const params = new URLSearchParams();
    if (q.trim()) params.set('q', q);
    if (filters.court && filters.court !== 'All courts') params.set('court', filters.court);
    if (filters.inFavour && filters.inFavour !== 'All') params.set('in_favour', filters.inFavour);
    return params;
  };

  const fetchParties = async (filters: { court: string; inFavour: string }) => {
    try {
      const params = new URLSearchParams();
      if (filters.court && filters.court !== 'All courts') params.set('court', filters.court);
      if (filters.inFavour && filters.inFavour !== 'All') params.set('in_favour', filters.inFavour);
      const res = await fetch(`${getApiUrl()}/search/parties?${params}`);
      if (!res.ok) return;
      const data: { petitioners: string[]; respondents: string[] } = await res.json();
      setAvailablePetitioners(data.petitioners);
      setAvailableRespondents(data.respondents);
    } catch {
      // silently ignore
    }
  };

  const fetchSections = async (act: string) => {
    if (!act) { setAvailableSections([]); setSectionFilter(''); return; }
    try {
      const params = new URLSearchParams({ act });
      const res = await fetch(`${getApiUrl()}/search/sections?${params}`);
      if (!res.ok) return;
      setAvailableSections(await res.json());
      setSectionFilter(''); // reset section when act changes
    } catch { /* silently ignore */ }
  };

  const performSearch = async (q: string, filters: { court: string; inFavour: string; actFilter: string; sectionFilter: string; petitioner: string; respondent: string; dateFrom: string; dateTo: string }, pageNum: number) => {
    setLoading(true);
    // Refresh party dropdowns on page 1 only if not yet loaded
    if (pageNum === 1 && availablePetitioners.length === 0) {
      fetchParties({ court: filters.court, inFavour: filters.inFavour });
    }
    try {
      const params = buildBaseParams(q, { court: filters.court, inFavour: filters.inFavour });
      if (filters.actFilter) params.set('act_filter', filters.actFilter);
      if (filters.actFilter && filters.sectionFilter) params.set('section_filter', filters.sectionFilter);
      if (filters.petitioner) params.set('petitioner', filters.petitioner);
      if (filters.respondent) params.set('respondent', filters.respondent);
      if (filters.dateFrom) params.set('date_from', filters.dateFrom);
      if (filters.dateTo) params.set('date_to', filters.dateTo);
      params.set('page', String(pageNum));
      params.set('page_size', String(PAGE_SIZE));
      const response = await fetch(`${getApiUrl()}/search?${params}`);
      if (!response.ok) throw new Error('Search failed');
      setResults(await response.json());
    } catch {
      setResults([]);
    } finally {
      setLoading(false);
      setHasSearched(true);
    }
  };

  useEffect(() => {
    const q = searchParams.get('q') || '';
    const c = searchParams.get('court') || '';
    setQuery(q);
    setCourt(c);
    setPage(1);
    didMount.current = true;
    performSearch(q, { court: c, inFavour, actFilter, sectionFilter, petitioner, respondent, dateFrom, dateTo }, 1);
  }, [searchParams]);

  const performActSearch = async (q: string) => {
    if (!q.trim()) return;
    setActSearchLoading(true);
    setActSearchQuery(q);
    setSelectedSection(null);
    try {
      const params = new URLSearchParams({ q, limit: '20' });
      const selectedAct = staticActList.find((a) => a.key === selectedActKey);
      if (selectedAct) params.set('act_name', selectedAct.name);
      const res = await fetch(`${getApiUrl()}/acts/search?${params}`);
      setActSearchResults(res.ok ? await res.json() : []);
    } catch {
      setActSearchResults([]);
    } finally {
      setActSearchLoading(false);
    }
  };

  const performRuleSearch = async (q: string) => {
    if (!q.trim()) return;
    setRuleSearchLoading(true);
    setRuleSearchQuery(q);
    setSelectedRuleSection(null);
    try {
      const params = new URLSearchParams({ q, limit: '20' });
      if (selectedRuleName) params.set('rule_name', selectedRuleName);
      const res = await fetch(`${getApiUrl()}/rules/search?${params}`);
      setRuleSearchResults(res.ok ? await res.json() : []);
    } catch {
      setRuleSearchResults([]);
    } finally {
      setRuleSearchLoading(false);
    }
  };

  const handleSearch = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (activeType === 'acts') {
      performActSearch(query);
      return;
    }
    if (activeType === 'rules') {
      performRuleSearch(query);
      return;
    }
    if (activeType === 'notifications') {
      if (!query.trim()) { setNotifSearchResults(null); return; }
      setNotifSearchLoading(true);
      searchNotifications(query)
        .then((r) => setNotifSearchResults(r))
        .catch(() => setNotifSearchResults([]))
        .finally(() => setNotifSearchLoading(false));
      return;
    }
    if (activeType === 'circulars') {
      if (!query.trim()) { setCircularSearchResults(null); return; }
      setCircularSearchLoading(true);
      searchCirculars(query)
        .then((r) => setCircularSearchResults(r))
        .catch(() => setCircularSearchResults([]))
        .finally(() => setCircularSearchLoading(false));
      return;
    }
    const params = new URLSearchParams();
    if (query) params.set('q', query);
    if (court && court !== 'All courts') params.set('court', court);
    router.push(`/?${params}`);
  };

  // When actFilter changes, reload section options then re-search
  useEffect(() => {
    if (!didMount.current) return;
    fetchSections(actFilter);
  }, [actFilter]);

  useEffect(() => {
    if (!didMount.current) return;
    fetchParties({ court, inFavour });
    setPage(1);
    performSearch(query, { court, inFavour, actFilter, sectionFilter, petitioner, respondent, dateFrom, dateTo }, 1);
  }, [court, inFavour, actFilter, sectionFilter, petitioner, respondent, dateFrom, dateTo]);

  const goToPage = (newPage: number) => {
    setPage(newPage);
    performSearch(query, { court, inFavour, actFilter, sectionFilter, petitioner, respondent, dateFrom, dateTo }, newPage);
  };

  const secondaryItems = buildSecondaryItems(activeType, acts, rules, notifications, circulars);
  const hasResults = results.length > 0;
  const showEmpty = !loading && !hasResults && hasSearched;
  const showSecondaryPanel = activeType !== 'case_laws';

  return (
    <div className="h-screen flex flex-col bg-[#f8f9fa]">
      {/* Top search bar */}
      <div className="bg-white border-b border-slate-200 px-5 py-3 shrink-0 flex items-center gap-3">
        <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center text-white font-bold text-sm select-none shrink-0">
          T
        </div>
        <form onSubmit={handleSearch} className="relative flex-1 max-w-2xl flex items-center gap-2">
          <div className="relative flex-1">
            <MagnifyingGlassIcon className="absolute left-3 top-2.5 w-4 h-4 text-slate-400 pointer-events-none" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={
                activeType === 'case_laws' ? 'Search case laws...' :
                activeType === 'acts'
                  ? selectedActKey
                    ? `Search in ${staticActList.find((a) => a.key === selectedActKey)?.name ?? 'selected act'}...`
                    : 'Search across all acts...'
                  : activeType === 'rules' ? (selectedRuleName ? `Search in ${selectedRuleName}...` : 'Search across all rules...') :
                  activeType === 'notifications' ? 'Search notifications...' :
                  activeType === 'circulars' ? 'Search circulars...' :
                  'Search...'
              }
              className="w-full pl-9 pr-4 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm
                focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none transition-all"
            />
            {loading && (
              <div className="absolute right-3 top-2.5 w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
            )}
          </div>
          <button
            type="submit"
            className="shrink-0 px-4 py-2 bg-blue-600 hover:bg-blue-700 active:bg-blue-800
              text-white text-sm font-medium rounded-lg transition-colors"
          >
            Search
          </button>
        </form>

        {activeType === 'case_laws' && (() => {
          const hasAdvanced = !!(petitioner || respondent || dateFrom || dateTo);
          return (
            <button
              onClick={() => setShowAdvanced(true)}
              className="flex items-center gap-1.5 px-3 py-2 text-sm text-slate-500 border border-slate-200
                rounded-lg hover:bg-slate-50 hover:text-slate-700 transition-colors shrink-0"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 6h9.75M10.5 6a1.5 1.5 0 11-3 0m3 0a1.5 1.5 0 10-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-9.75 0h9.75" />
              </svg>
              Advanced Search
              {hasAdvanced && <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />}
            </button>
          );
        })()}
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* ── Content Type Panel ─────────────────────────────────────────────── */}
        <aside className="w-52 shrink-0 bg-white border-r border-slate-200 overflow-y-auto">
          <div className="p-4">
            <h3 className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-3">
              Content Type
            </h3>
            <nav className="space-y-0.5">
              {CONTENT_TYPES.map(({ id, label }) => (
                <button
                  key={id}
                  onClick={() => setActiveType(id)}
                  className={`w-full text-left flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors ${
                    activeType === id
                      ? 'bg-blue-50 text-blue-700 font-medium'
                      : 'text-slate-600 hover:bg-slate-50'
                  }`}
                >
                  <ContentTypeIcon type={id} active={activeType === id} />
                  {label}
                </button>
              ))}
            </nav>
          </div>
        </aside>

        {/* ── Secondary Nav Panel (Acts / Rules / Notifications / Circulars) ── */}
        {showSecondaryPanel && (
          <aside className="w-72 shrink-0 bg-white border-r border-slate-200 flex flex-col overflow-hidden">
            {activeType === 'acts' ? (
              <>
                {/* Act selector dropdown */}
                <div className="p-3 border-b border-slate-100 shrink-0">
                  <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider block mb-1.5">
                    Select Act
                  </label>
                  <select
                    value={selectedActKey}
                    onChange={(e) => { setSelectedActKey(e.target.value); setSelectedSection(null); }}
                    className="w-full px-2 py-1.5 bg-slate-50 border border-slate-200 rounded-lg text-xs
                      text-slate-700 focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none"
                  >
                    {staticActList.map((a) => (
                      <option key={a.key} value={a.key}>{a.name}</option>
                    ))}
                  </select>
                </div>
                {/* Sections list */}
                <div className="flex-1 overflow-y-auto p-2">
                  {actSectionsLoading ? (
                    <p className="text-xs text-slate-400 px-2 py-1">Loading…</p>
                  ) : actSections.length === 0 ? (
                    <p className="text-xs text-slate-400 px-2 py-1">No sections found</p>
                  ) : (
                    <nav className="space-y-0.5">
                      {actSections.map((s) => (
                        <button
                          key={s.idx}
                          onClick={() => loadSection(s.idx)}
                          className={`w-full text-left px-2 py-2 rounded-lg text-sm transition-colors ${
                            selectedSection?.idx === s.idx
                              ? 'bg-blue-50 text-blue-700 font-medium'
                              : 'text-slate-600 hover:bg-slate-50 hover:text-blue-700'
                          }`}
                          title={s.section_name ?? undefined}
                        >
                          <span className="font-semibold">{s.section_no}</span>
                          {s.section_name && (
                            <span className="block text-xs text-slate-400 truncate">{s.section_name}</span>
                          )}
                        </button>
                      ))}
                    </nav>
                  )}
                </div>
              </>
            ) : activeType === 'rules' ? (
              <>
                {/* Rule set selector */}
                <div className="p-3 border-b border-slate-100 shrink-0">
                  <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider block mb-1.5">
                    Select Rules
                  </label>
                  <select
                    value={selectedRuleName}
                    onChange={(e) => { setSelectedRuleName(e.target.value); setSelectedRuleSection(null); setRuleSearchQuery(''); setRuleSearchResults([]); }}
                    className="w-full px-2 py-1.5 bg-slate-50 border border-slate-200 rounded-lg text-xs
                      text-slate-700 focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none"
                  >
                    {Array.from(new Set(rules.map((r) => r.act_name))).map((name) => (
                      <option key={name} value={name}>{name}</option>
                    ))}
                  </select>
                </div>
                {/* Rules list */}
                <div className="flex-1 overflow-y-auto p-2">
                  {rules.length === 0 ? (
                    <p className="text-xs text-slate-400 px-2 py-1">Loading…</p>
                  ) : (
                    <nav className="space-y-0.5">
                      {rules.filter((r) => r.act_name === selectedRuleName).map((r) => (
                        <button
                          key={r.primary_id}
                          onClick={() => {
                            setRuleSectionLoading(true);
                            fetch(`${getApiUrl()}/library/rules/${r.primary_id}`)
                              .then((res) => res.ok ? res.json() : null)
                              .then((s) => s && setSelectedRuleSection({ ...s, matchedSubSection: undefined }))
                              .catch(() => {})
                              .finally(() => setRuleSectionLoading(false));
                          }}
                          className={`w-full text-left px-2 py-2 rounded-lg text-sm transition-colors ${
                            selectedRuleSection?.primary_id === r.primary_id && !ruleSearchQuery
                              ? 'bg-blue-50 text-blue-700 font-medium'
                              : 'text-slate-600 hover:bg-slate-50 hover:text-blue-700'
                          }`}
                          title={r.section_name ?? undefined}
                        >
                          <span className="font-semibold">{r.section_no}</span>
                          {r.section_name && (
                            <span className="block text-xs text-slate-400 truncate">{r.section_name}</span>
                          )}
                        </button>
                      ))}
                    </nav>
                  )}
                </div>
              </>
            ) : activeType === 'notifications' ? (
              <>
                <>
                <div className="px-3 py-3 border-b border-slate-100 shrink-0">
                  <div className="mb-2">
                    <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider block mb-1">Year</label>
                    <select
                      value={notifYear ?? ''}
                      onChange={(e) => { setNotifYear(e.target.value ? Number(e.target.value) : null); setSelectedNotification(null); }}
                      className="w-full text-xs px-2 py-1.5 bg-slate-50 border border-slate-200 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none"
                    >
                      <option value="">All years</option>
                      {[2025,2024,2023,2022,2021,2020,2019,2018,2017].map((y) => (
                        <option key={y} value={y}>{y}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider block mb-1">Category</label>
                    <select
                      value={notifCategory ?? ''}
                      onChange={(e) => { setNotifCategory(e.target.value || null); setSelectedNotification(null); }}
                      className="w-full text-xs px-2 py-1.5 bg-slate-50 border border-slate-200 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none"
                    >
                      <option value="">All categories</option>
                      {['Central Tax','Central Tax (Rate)','Compensation Cess','Compensation Cess (Rate)','Integrated Tax','Integrated Tax (Rate)','Union Territory Tax','Union Territory Tax (Rate)'].map((c) => (
                        <option key={c} value={c}>{c}</option>
                      ))}
                    </select>
                  </div>
                  <label className="flex items-center gap-2 mt-2 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={notifActiveOnly}
                      onChange={(e) => { setNotifActiveOnly(e.target.checked); setSelectedNotification(null); }}
                      className="w-3.5 h-3.5 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                    />
                    <span className="text-xs text-slate-600">Active only</span>
                  </label>
                </div>
                <div className="flex-1 overflow-y-auto">
                  {(() => {
                    const filtered = notifications.filter((n) =>
                      (notifYear === null || n.year === notifYear) &&
                      (notifCategory === null || n.category === notifCategory) &&
                      (!notifActiveOnly || n.is_active)
                    );
                    return filtered.length === 0 ? (
                      <p className="text-xs text-slate-400 text-center py-10">No notifications found.</p>
                    ) : (
                      <ul className="divide-y divide-slate-100">
                        {filtered.map((item) => (
                          <li key={item.primary_id}>
                            <button
                              onClick={() => setSelectedNotification(item)}
                              className={`w-full text-left px-3 py-2.5 transition-colors border-l-2 ${
                                selectedNotification?.primary_id === item.primary_id
                                  ? 'bg-blue-50 border-blue-500'
                                  : 'border-transparent hover:bg-slate-50'
                              }`}
                            >
                              <p className="text-xs font-semibold text-slate-700 truncate">{item.notification_no}</p>
                              {item.title && <p className="text-xs text-slate-400 mt-0.5 line-clamp-2 leading-relaxed">{item.title}</p>}
                              {item.category && <p className="text-[10px] text-slate-300 mt-0.5">{item.category}</p>}
                            </button>
                          </li>
                        ))}
                      </ul>
                    );
                  })()}
                </div>
                <div className="px-3 py-1.5 border-t border-slate-100 shrink-0">
                  <p className="text-[10px] text-slate-400">
                    {notifications.filter((n) => (notifYear === null || n.year === notifYear) && (notifCategory === null || n.category === notifCategory) && (!notifActiveOnly || n.is_active)).length} notifications
                  </p>
                </div>
                </>
              </>
            ) : activeType === 'circulars' ? (
              <>
                <>
                <div className="px-3 py-3 border-b border-slate-100 shrink-0">
                  <div className="mb-2">
                    <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider block mb-1">Year</label>
                    <select
                      value={circularYear ?? ''}
                      onChange={(e) => { setCircularYear(e.target.value ? Number(e.target.value) : null); setSelectedCircular(null); }}
                      className="w-full text-xs px-2 py-1.5 bg-slate-50 border border-slate-200 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none"
                    >
                      <option value="">All years</option>
                      {[2025,2024,2023,2022,2021,2020,2019,2018,2017].map((y) => (
                        <option key={y} value={y}>{y}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider block mb-1">Category</label>
                    <select
                      value={circularCategory ?? ''}
                      onChange={(e) => { setCircularCategory(e.target.value || null); setSelectedCircular(null); }}
                      className="w-full text-xs px-2 py-1.5 bg-slate-50 border border-slate-200 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none"
                    >
                      <option value="">All categories</option>
                      {['CGST','IGST','CESS'].map((c) => (
                        <option key={c} value={c}>{c}</option>
                      ))}
                    </select>
                  </div>
                  <label className="flex items-center gap-2 mt-2 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={circularActiveOnly}
                      onChange={(e) => { setCircularActiveOnly(e.target.checked); setSelectedCircular(null); }}
                      className="w-3.5 h-3.5 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                    />
                    <span className="text-xs text-slate-600">Active only</span>
                  </label>
                </div>
                <div className="flex-1 overflow-y-auto">
                  {(() => {
                    const filtered = circulars.filter((c) =>
                      (circularYear === null || c.year === circularYear) &&
                      (circularCategory === null || c.category === circularCategory) &&
                      (!circularActiveOnly || c.is_active)
                    );
                    return filtered.length === 0 ? (
                      <p className="text-xs text-slate-400 text-center py-10">No circulars found.</p>
                    ) : (
                      <ul className="divide-y divide-slate-100">
                        {filtered.map((item) => (
                          <li key={item.primary_id}>
                            <button
                              onClick={() => setSelectedCircular(item)}
                              className={`w-full text-left px-3 py-2.5 transition-colors border-l-2 ${
                                selectedCircular?.primary_id === item.primary_id
                                  ? 'bg-blue-50 border-blue-500'
                                  : 'border-transparent hover:bg-slate-50'
                              }`}
                            >
                              <p className="text-xs font-semibold text-slate-700 truncate">{item.circular_no}</p>
                              {item.subject && <p className="text-xs text-slate-400 mt-0.5 line-clamp-2 leading-relaxed">{item.subject}</p>}
                              {item.category && <p className="text-[10px] text-slate-300 mt-0.5">{item.category}</p>}
                            </button>
                          </li>
                        ))}
                      </ul>
                    );
                  })()}
                </div>
                <div className="px-3 py-1.5 border-t border-slate-100 shrink-0">
                  <p className="text-[10px] text-slate-400">
                    {circulars.filter((c) => (circularYear === null || c.year === circularYear) && (circularCategory === null || c.category === circularCategory) && (!circularActiveOnly || c.is_active)).length} circulars
                  </p>
                </div>
                </>
              </>
            ) : null}
          </aside>
        )}

        {/* ── Main Results Area ─────────────────────────────────────────────── */}
        <main className="flex-1 overflow-y-auto flex flex-col min-w-0">
          {/* Filter bar — Case Laws only */}
          {activeType === 'case_laws' && (
            <div className="bg-white border-b border-slate-200 shrink-0">
              <div className="px-6 py-3 flex items-center gap-6">
                <FilterSelect
                  label="In Favour Of"
                  value={inFavour}
                  onChange={setInFavour}
                  options={IN_FAVOUR}
                  placeholder="All"
                />
                <FilterSelect
                  label="Court"
                  value={court}
                  onChange={setCourt}
                  options={COURTS}
                  placeholder="All courts"
                />
                {availableActs.length > 0 && (
                  <FilterSelect
                    label="Act / Rules"
                    value={actFilter}
                    onChange={(v) => { setActFilter(v); setSectionFilter(''); }}
                    options={[ACT_PLACEHOLDER, ...availableActs]}
                    placeholder={ACT_PLACEHOLDER}
                  />
                )}
                {actFilter && availableSections.length > 0 && (
                  <div className="flex flex-col gap-0.5">
                    <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">Section</span>
                    <SectionCombobox
                      value={sectionFilter}
                      onChange={setSectionFilter}
                      options={availableSections}
                    />
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Advanced Search Dialog */}
          {showAdvanced && (
            <AdvancedSearchDialog
              petitioner={petitioner}
              respondent={respondent}
              dateFrom={dateFrom}
              dateTo={dateTo}
              availablePetitioners={availablePetitioners}
              availableRespondents={availableRespondents}
              onApply={(filters) => {
                setPetitioner(filters.petitioner);
                setRespondent(filters.respondent);
                setDateFrom(filters.dateFrom);
                setDateTo(filters.dateTo);
                setShowAdvanced(false);
              }}
              onClose={() => setShowAdvanced(false)}
            />
          )}

          {/* Acts viewer / search results */}
          {activeType === 'acts' && (
            <div className="flex-1 overflow-y-auto p-6">
              {/* Search results mode */}
              {actSearchQuery && !selectedSection ? (
                actSearchLoading ? (
                  <div className="flex items-center justify-center h-64">
                    <div className="w-7 h-7 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                  </div>
                ) : actSearchResults.length === 0 ? (
                  <div className="flex items-center justify-center h-64">
                    <p className="text-sm text-slate-400">No sections found for &ldquo;{actSearchQuery}&rdquo;</p>
                  </div>
                ) : (
                  <>
                    <div className="mb-4">
                      <p className="text-sm text-slate-500">
                        <span className="font-semibold text-slate-800">{actSearchResults.length}</span>
                        {' '}result{actSearchResults.length !== 1 ? 's' : ''} for{' '}
                        <span className="font-medium text-slate-700">&ldquo;{actSearchQuery}&rdquo;</span>
                      </p>
                    </div>
                    <div className="space-y-2">
                      {actSearchResults.map((hit) => (
                        <button
                          key={`${hit.act_id}`}
                          onClick={() => {
                            setSectionLoading(true);
                            fetch(`${getApiUrl()}/library/acts/${hit.primary_id}`)
                              .then((r) => r.ok ? r.json() : null)
                              .then((s) => s && setSelectedSection({ ...s, html: s.html ?? s.html_content ?? null, idx: -1, matchedSubSection: hit.matched_sub_section }))
                              .catch(() => {})
                              .finally(() => setSectionLoading(false));
                          }}
                          className="w-full text-left bg-white border border-slate-200 rounded-xl p-4
                            hover:border-blue-300 hover:shadow-sm transition-all"
                        >
                          <p className="text-xs text-slate-400 mb-0.5">
                            {hit.act_name}
                            {hit.chapter_no && ` · ${hit.chapter_no}`}
                          </p>
                          <p className="text-sm font-semibold text-slate-800">
                            {hit.section_no}
                            {hit.section_name ? `: ${hit.section_name}` : ''}
                          </p>
                          {hit.matched_sub_section !== hit.section_no && (
                            <p className="text-xs text-amber-600 mt-0.5 font-medium">
                              Match in {hit.matched_sub_section}
                            </p>
                          )}
                          {hit.snippet && (
                            <p className="text-xs text-slate-500 mt-1 line-clamp-2 leading-relaxed">
                              {hit.snippet}
                            </p>
                          )}
                        </button>
                      ))}
                    </div>
                  </>
                )
              ) : sectionLoading ? (
                <div className="flex items-center justify-center h-64">
                  <div className="w-7 h-7 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                </div>
              ) : selectedSection ? (
                <div className="max-w-3xl">
                  {actSearchQuery && (
                    <button
                      onClick={() => setSelectedSection(null)}
                      className="mb-4 flex items-center gap-1.5 text-xs text-slate-400 hover:text-blue-600 transition-colors"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
                      </svg>
                      Back to results
                    </button>
                  )}
                  <div className="mb-4">
                    <p className="text-xs text-slate-400 mb-1">
                      {selectedSection.act_name}
                      {selectedSection.chapter_no && ` · ${selectedSection.chapter_no}`}
                      {selectedSection.chapter_name && ` — ${selectedSection.chapter_name}`}
                    </p>
                    <h1 className="text-xl font-semibold text-slate-800">
                      {selectedSection.section_no}
                      {selectedSection.section_name && `: ${selectedSection.section_name}`}
                    </h1>
                  </div>
                  {selectedSection.html ? (
                    <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
                      <div
                        className="text-sm leading-relaxed text-slate-700
                          [&_p]:mb-3 [&_strong]:font-semibold [&_sup]:text-[10px] [&_sup]:font-semibold
                          [&_table]:w-full [&_table]:border-collapse [&_table]:my-4 [&_table]:text-sm
                          [&_th]:border [&_th]:border-slate-300 [&_th]:bg-slate-50 [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_th]:font-semibold
                          [&_td]:border [&_td]:border-slate-300 [&_td]:px-3 [&_td]:py-2 [&_td]:align-top
                          [&_tr:nth-child(even)_td]:bg-slate-50/50"
                        dangerouslySetInnerHTML={{ __html: processActHtml(selectedSection.html, selectedSection.section_no, selectedSection.matchedSubSection) }}
                      />
                    </div>
                  ) : (
                    <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
                      <pre className="whitespace-pre-wrap text-sm text-slate-700 leading-relaxed">
                        {selectedSection.content}
                      </pre>
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-center">
                  <svg className="w-10 h-10 text-slate-200 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25" />
                  </svg>
                  <p className="text-sm text-slate-400">Select a section from the sidebar or search above</p>
                </div>
              )}
            </div>
          )}

          {/* Rules viewer / search results */}
          {activeType === 'rules' && (
            <div className="flex-1 overflow-y-auto p-6">
              {ruleSearchQuery && !selectedRuleSection ? (
                ruleSearchLoading ? (
                  <div className="flex items-center justify-center h-64">
                    <div className="w-7 h-7 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                  </div>
                ) : ruleSearchResults.length === 0 ? (
                  <div className="flex items-center justify-center h-64">
                    <p className="text-sm text-slate-400">No rules found for &ldquo;{ruleSearchQuery}&rdquo;</p>
                  </div>
                ) : (
                  <>
                    <div className="mb-4">
                      <p className="text-sm text-slate-500">
                        <span className="font-semibold text-slate-800">{ruleSearchResults.length}</span>
                        {' '}result{ruleSearchResults.length !== 1 ? 's' : ''} for{' '}
                        <span className="font-medium text-slate-700">&ldquo;{ruleSearchQuery}&rdquo;</span>
                      </p>
                    </div>
                    <div className="space-y-2">
                      {ruleSearchResults.map((hit) => (
                        <button
                          key={hit.rule_id}
                          onClick={() => {
                            setRuleSectionLoading(true);
                            fetch(`${getApiUrl()}/library/rules/${hit.primary_id}`)
                              .then((r) => r.ok ? r.json() : null)
                              .then((s) => s && setSelectedRuleSection({ ...s, matchedSubSection: hit.matched_sub_section }))
                              .catch(() => {})
                              .finally(() => setRuleSectionLoading(false));
                          }}
                          className="w-full text-left bg-white border border-slate-200 rounded-xl p-4
                            hover:border-blue-300 hover:shadow-sm transition-all"
                        >
                          <p className="text-xs text-slate-400 mb-0.5">{hit.rule_name}</p>
                          <p className="text-sm font-semibold text-slate-800">
                            {hit.section_no}{hit.section_name ? `: ${hit.section_name}` : ''}
                          </p>
                          {hit.matched_sub_section !== hit.section_no && (
                            <p className="text-xs text-amber-600 mt-0.5 font-medium">Match in {hit.matched_sub_section}</p>
                          )}
                          {hit.snippet && (
                            <p className="text-xs text-slate-500 mt-1 line-clamp-2 leading-relaxed">{hit.snippet}</p>
                          )}
                        </button>
                      ))}
                    </div>
                  </>
                )
              ) : ruleSectionLoading ? (
                <div className="flex items-center justify-center h-64">
                  <div className="w-7 h-7 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                </div>
              ) : selectedRuleSection ? (
                <div className="max-w-3xl">
                  {ruleSearchQuery && (
                    <button
                      onClick={() => setSelectedRuleSection(null)}
                      className="mb-4 flex items-center gap-1.5 text-xs text-slate-400 hover:text-blue-600 transition-colors"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
                      </svg>
                      Back to results
                    </button>
                  )}
                  <div className="mb-4">
                    <p className="text-xs text-slate-400 mb-1">{selectedRuleSection.act_name}</p>
                    <h1 className="text-xl font-semibold text-slate-800">
                      {selectedRuleSection.section_no}
                      {selectedRuleSection.section_name && `: ${selectedRuleSection.section_name}`}
                    </h1>
                  </div>
                  {selectedRuleSection.html_content ? (
                    <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
                      <div
                        className="text-sm leading-relaxed text-slate-700
                          [&_p]:mb-3 [&_strong]:font-semibold [&_sup]:text-[10px] [&_sup]:font-semibold
                          [&_table]:w-full [&_table]:border-collapse [&_table]:my-4 [&_table]:text-sm
                          [&_th]:border [&_th]:border-slate-300 [&_th]:bg-slate-50 [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_th]:font-semibold
                          [&_td]:border [&_td]:border-slate-300 [&_td]:px-3 [&_td]:py-2 [&_td]:align-top
                          [&_tr:nth-child(even)_td]:bg-slate-50/50"
                        dangerouslySetInnerHTML={{ __html: processActHtml(selectedRuleSection.html_content, selectedRuleSection.section_no, selectedRuleSection.matchedSubSection) }}
                      />
                    </div>
                  ) : (
                    <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
                      <pre className="whitespace-pre-wrap text-sm text-slate-700 leading-relaxed">
                        {selectedRuleSection.content}
                      </pre>
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-center">
                  <svg className="w-10 h-10 text-slate-200 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25H12" />
                  </svg>
                  <p className="text-sm text-slate-400">Select a rule from the sidebar or search above</p>
                </div>
              )}
            </div>
          )}

          {/* Notifications PDF viewer */}
          {activeType === 'notifications' && (
            <div className="flex-1 overflow-hidden flex">
              {selectedNotification ? (
                <>
                  <div className="flex-1 flex flex-col overflow-hidden">
                    <div className="px-4 py-2 border-b border-slate-200 bg-white shrink-0 flex items-center gap-2">
                      <button
                        onClick={() => setSelectedNotification(null)}
                        className="text-xs text-blue-600 hover:underline"
                      >
                        ← Back to results
                      </button>
                      <span className="text-xs text-slate-400">|</span>
                      <span className="text-xs text-slate-600 font-medium">{selectedNotification.notification_no}</span>
                    </div>
                    <iframe
                      key={selectedNotification.primary_id}
                      src={getLibraryPdfUrl('notification', selectedNotification.primary_id)}
                      className="flex-1 border-0"
                      title={selectedNotification.notification_no}
                    />
                  </div>
                  <aside className="w-72 shrink-0 border-l border-slate-200 bg-white overflow-y-auto p-5">
                    <h3 className="text-sm font-semibold text-slate-700 mb-4">Details</h3>
                    <dl className="space-y-4 text-sm">
                      <div>
                        <dt className="text-[10px] uppercase tracking-wide text-slate-400 mb-0.5">Notification No.</dt>
                        <dd className="font-medium text-slate-800">{notificationDetail?.notification_no ?? selectedNotification.notification_no}</dd>
                      </div>
                      {(notificationDetail?.category ?? selectedNotification.category) && (
                        <div>
                          <dt className="text-xs uppercase tracking-wide text-slate-400 mb-0.5">Category</dt>
                          <dd className="font-medium text-slate-800">{notificationDetail?.category ?? selectedNotification.category}</dd>
                        </div>
                      )}
                      {notificationDetail?.issued_on && (
                        <div>
                          <dt className="text-xs uppercase tracking-wide text-slate-400 mb-0.5">Issued On</dt>
                          <dd className="font-medium text-slate-800">{new Date(notificationDetail.issued_on).toLocaleDateString()}</dd>
                        </div>
                      )}
                      {(notificationDetail?.title ?? selectedNotification.title) && (
                        <div>
                          <dt className="text-xs uppercase tracking-wide text-slate-400 mb-0.5">Subject</dt>
                          <dd className="text-slate-700 leading-relaxed">{notificationDetail?.title ?? selectedNotification.title}</dd>
                        </div>
                      )}
                      {notificationDetail && (
                        <>
                          <div>
                            <dt className="text-xs uppercase tracking-wide text-slate-400 mb-0.5">Active</dt>
                            <dd className={`font-medium ${notificationDetail.is_active ? 'text-emerald-600' : 'text-slate-500'}`}>
                              {notificationDetail.is_active ? 'Yes' : 'No'}
                            </dd>
                          </div>
                          <div>
                            <dt className="text-xs uppercase tracking-wide text-slate-400 mb-0.5">Amended</dt>
                            <dd className={`font-medium ${notificationDetail.is_amended ? 'text-amber-600' : 'text-slate-500'}`}>
                              {notificationDetail.is_amended ? 'Yes' : 'No'}
                            </dd>
                          </div>
                        </>
                      )}
                    </dl>
                  </aside>
                </>
              ) : notifSearchResults !== null ? (
                <div className="flex-1 overflow-y-auto p-6">
                  {notifSearchLoading ? (
                    <div className="flex items-center justify-center h-40">
                      <div className="w-5 h-5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                    </div>
                  ) : (
                    <>
                      <div className="flex items-center justify-between mb-4">
                        <span className="text-sm text-slate-500">
                          <span className="font-semibold text-slate-700">
                            {notifSearchResults.filter(n => !notifActiveOnly || n.is_active).length}
                          </span>
                          {' '}result{notifSearchResults.filter(n => !notifActiveOnly || n.is_active).length !== 1 ? 's' : ''} for{' '}
                          <span className="font-medium text-slate-700">&ldquo;{query}&rdquo;</span>
                        </span>
                        <button
                          onClick={() => setNotifActiveOnly(v => !v)}
                          className={`text-xs px-3 py-1 rounded-full border transition-colors ${
                            notifActiveOnly
                              ? 'bg-emerald-50 border-emerald-300 text-emerald-700 font-medium'
                              : 'bg-white border-slate-200 text-slate-500 hover:border-slate-300'
                          }`}
                        >
                          Active only
                        </button>
                      </div>
                  {notifSearchResults.filter(n => !notifActiveOnly || n.is_active).length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-40 text-center">
                      <p className="text-sm text-slate-400">No notifications found.</p>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {notifSearchResults.filter(n => !notifActiveOnly || n.is_active).map((item) => (
                        <button
                          key={item.primary_id}
                          onClick={() => setSelectedNotification(item)}
                          className="w-full text-left bg-white border border-slate-200 rounded-xl p-4
                            hover:border-blue-300 hover:shadow-sm transition-all"
                        >
                          <div className="flex items-center justify-between mb-0.5">
                            <p className="text-xs text-slate-400">
                              {[item.category, item.year].filter(Boolean).join(' · ')}
                            </p>
                            {item.is_active === false && (
                              <span className="text-[10px] font-semibold uppercase tracking-wide
                                text-red-600 bg-red-50 border border-red-200 rounded px-1.5 py-0.5">
                                Withdrawn
                              </span>
                            )}
                          </div>
                          <p className="text-sm font-semibold text-slate-800">{item.notification_no}</p>
                          {item.title && (
                            <p className="text-xs text-slate-500 mt-1 line-clamp-2 leading-relaxed">{item.title}</p>
                          )}
                        </button>
                      ))}
                    </div>
                  )}
                    </>
                  )}
                </div>
              ) : (
                <div className="flex-1 flex flex-col items-center justify-center text-center">
                  <svg className="w-10 h-10 text-slate-200 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75v-.7V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0" />
                  </svg>
                  <p className="text-sm text-slate-400">Search notifications using the bar above, or browse using the panel on the left</p>
                </div>
              )}
            </div>
          )}

          {/* Circulars PDF viewer */}
          {activeType === 'circulars' && (
            <div className="flex-1 overflow-hidden flex">
              {selectedCircular ? (
                <>
                  <div className="flex-1 flex flex-col overflow-hidden">
                    <div className="px-4 py-2 border-b border-slate-200 bg-white shrink-0 flex items-center gap-2">
                      <button
                        onClick={() => setSelectedCircular(null)}
                        className="text-xs text-blue-600 hover:underline"
                      >
                        ← Back to results
                      </button>
                      <span className="text-xs text-slate-400">|</span>
                      <span className="text-xs text-slate-600 font-medium">{selectedCircular.circular_no}</span>
                    </div>
                    <iframe
                      key={selectedCircular.primary_id}
                      src={getLibraryPdfUrl('circular', selectedCircular.primary_id)}
                      className="flex-1 border-0"
                      title={selectedCircular.circular_no}
                    />
                  </div>
                  <aside className="w-72 shrink-0 border-l border-slate-200 bg-white overflow-y-auto p-5">
                    <h3 className="text-sm font-semibold text-slate-700 mb-4">Details</h3>
                    <dl className="space-y-4 text-sm">
                      <div>
                        <dt className="text-[10px] uppercase tracking-wide text-slate-400 mb-0.5">Circular No.</dt>
                        <dd className="font-medium text-slate-800">{circularDetail?.circular_no ?? selectedCircular.circular_no}</dd>
                      </div>
                      {(circularDetail?.category ?? selectedCircular.category) && (
                        <div>
                          <dt className="text-xs uppercase tracking-wide text-slate-400 mb-0.5">Category</dt>
                          <dd className="font-medium text-slate-800">{circularDetail?.category ?? selectedCircular.category}</dd>
                        </div>
                      )}
                      {circularDetail?.issued_on && (
                        <div>
                          <dt className="text-xs uppercase tracking-wide text-slate-400 mb-0.5">Issued On</dt>
                          <dd className="font-medium text-slate-800">{new Date(circularDetail.issued_on).toLocaleDateString()}</dd>
                        </div>
                      )}
                      {(circularDetail?.subject ?? selectedCircular.subject) && (
                        <div>
                          <dt className="text-xs uppercase tracking-wide text-slate-400 mb-0.5">Subject</dt>
                          <dd className="text-slate-700 leading-relaxed">{circularDetail?.subject ?? selectedCircular.subject}</dd>
                        </div>
                      )}
                      {circularDetail && (
                        <>
                          <div>
                            <dt className="text-xs uppercase tracking-wide text-slate-400 mb-0.5">Active</dt>
                            <dd className={`font-medium ${circularDetail.is_active ? 'text-emerald-600' : 'text-slate-500'}`}>
                              {circularDetail.is_active ? 'Yes' : 'No'}
                            </dd>
                          </div>
                          <div>
                            <dt className="text-xs uppercase tracking-wide text-slate-400 mb-0.5">Amended</dt>
                            <dd className={`font-medium ${circularDetail.is_amended ? 'text-amber-600' : 'text-slate-500'}`}>
                              {circularDetail.is_amended ? 'Yes' : 'No'}
                            </dd>
                          </div>
                        </>
                      )}
                    </dl>
                  </aside>
                </>
              ) : circularSearchResults !== null ? (
                <div className="flex-1 overflow-y-auto p-6">
                  {circularSearchLoading ? (
                    <div className="flex items-center justify-center h-40">
                      <div className="w-5 h-5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                    </div>
                  ) : (
                    <>
                      <div className="flex items-center justify-between mb-4">
                        <span className="text-sm text-slate-500">
                          <span className="font-semibold text-slate-700">
                            {circularSearchResults.filter(c => !circularActiveOnly || c.is_active).length}
                          </span>
                          {' '}result{circularSearchResults.filter(c => !circularActiveOnly || c.is_active).length !== 1 ? 's' : ''} for{' '}
                          <span className="font-medium text-slate-700">&ldquo;{query}&rdquo;</span>
                        </span>
                        <button
                          onClick={() => setCircularActiveOnly(v => !v)}
                          className={`text-xs px-3 py-1 rounded-full border transition-colors ${
                            circularActiveOnly
                              ? 'bg-emerald-50 border-emerald-300 text-emerald-700 font-medium'
                              : 'bg-white border-slate-200 text-slate-500 hover:border-slate-300'
                          }`}
                        >
                          Active only
                        </button>
                      </div>
                      {circularSearchResults.filter(c => !circularActiveOnly || c.is_active).length === 0 ? (
                        <div className="flex flex-col items-center justify-center h-40 text-center">
                          <p className="text-sm text-slate-400">No circulars found.</p>
                        </div>
                      ) : (
                        <div className="space-y-2">
                          {circularSearchResults.filter(c => !circularActiveOnly || c.is_active).map((item) => (
                            <button
                              key={item.primary_id}
                              onClick={() => setSelectedCircular(item)}
                              className="w-full text-left bg-white border border-slate-200 rounded-xl p-4
                                hover:border-blue-300 hover:shadow-sm transition-all"
                            >
                              <div className="flex items-center justify-between mb-0.5">
                                <p className="text-xs text-slate-400">
                                  {[item.category, item.year].filter(Boolean).join(' · ')}
                                </p>
                                {item.is_active === false && (
                                  <span className="text-[10px] font-semibold uppercase tracking-wide
                                    text-red-600 bg-red-50 border border-red-200 rounded px-1.5 py-0.5">
                                    Withdrawn
                                  </span>
                                )}
                              </div>
                              <p className="text-sm font-semibold text-slate-800">{item.circular_no}</p>
                              {item.subject && (
                                <p className="text-xs text-slate-500 mt-1 line-clamp-2 leading-relaxed">{item.subject}</p>
                              )}
                            </button>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                </div>
              ) : (
                <div className="flex-1 flex flex-col items-center justify-center text-center">
                  <svg className="w-10 h-10 text-slate-200 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                  </svg>
                  <p className="text-sm text-slate-400">Search circulars using the bar above, or browse using the panel on the left</p>
                </div>
              )}
            </div>
          )}

          {/* Results */}
          {activeType !== 'acts' && activeType !== 'rules' && activeType !== 'notifications' && activeType !== 'circulars' && <div className="flex-1 overflow-y-auto p-6">
            {hasResults ? (
              <>
                <div className="mb-4 flex items-center justify-between">
                  <p className="text-sm text-slate-500">
                    <span className="font-semibold text-slate-800">
                      {(page - 1) * PAGE_SIZE + results.length}
                    </span>
                    {query.trim() ? (
                      <>{' '}result{results.length !== 1 ? 's' : ''} for{' '}
                      <span className="font-medium text-slate-700">&ldquo;{query}&rdquo;</span></>
                    ) : (
                      <> case laws &mdash; page {page}</>
                    )}
                  </p>
                </div>
                <div className="space-y-3">
                  {results.map((result: any, index: number) => (
                    <div
                      key={result.id}
                      className="animate-in fade-in slide-in-from-bottom-2 duration-300"
                      style={{ animationDelay: `${index * 40}ms` }}
                    >
                      <SearchResult result={result} />
                    </div>
                  ))}
                </div>
                {/* Pagination controls */}
                <div className="flex items-center justify-center gap-3 mt-6">
                  <button
                    onClick={() => goToPage(page - 1)}
                    disabled={page === 1 || loading}
                    className="px-3 py-1.5 text-sm rounded-lg border border-slate-200 text-slate-600
                      hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                  >
                    Previous
                  </button>
                  <span className="text-sm text-slate-500">Page {page}</span>
                  <button
                    onClick={() => goToPage(page + 1)}
                    disabled={results.length < PAGE_SIZE || loading}
                    className="px-3 py-1.5 text-sm rounded-lg border border-slate-200 text-slate-600
                      hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                  >
                    Next
                  </button>
                </div>
              </>
            ) : loading ? (
              <div className="flex items-center justify-center h-64">
                <div className="w-7 h-7 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
              </div>
            ) : showEmpty ? (
              <div className="flex flex-col items-center justify-center h-64 gap-2">
                <p className="text-sm text-slate-500 font-medium">No results found</p>
                <p className="text-xs text-slate-400 text-center max-w-sm">
                  {[query.trim(), petitioner, respondent].filter(Boolean).join(' + ')}
                  {(query.trim() || petitioner || respondent) ? ' — try broadening your search or adjusting filters.' : 'No documents match the selected filters.'}
                </p>
              </div>
            ) : null}
          </div>}
        </main>
      </div>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function ContentTypeIcon({ type, active }: { type: ContentType; active: boolean }) {
  const cls = `w-4 h-4 shrink-0 ${active ? 'text-blue-600' : 'text-slate-400'}`;
  if (type === 'case_laws')
    return (
      <svg className={cls} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
      </svg>
    );
  if (type === 'acts')
    return (
      <svg className={cls} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25" />
      </svg>
    );
  if (type === 'rules')
    return (
      <svg className={cls} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25H12" />
      </svg>
    );
  if (type === 'notifications')
    return (
      <svg className={cls} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75v-.7V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0" />
      </svg>
    );
  // circulars
  return (
    <svg className={cls} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
    </svg>
  );
}

function todayLocalISO(): string {
  const d = new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

function AdvancedSearchDialog({
  petitioner, respondent, dateFrom, dateTo,
  availablePetitioners, availableRespondents,
  onApply, onClose,
}: {
  petitioner: string; respondent: string;
  dateFrom: string; dateTo: string;
  availablePetitioners: string[]; availableRespondents: string[];
  onApply: (f: { petitioner: string; respondent: string; dateFrom: string; dateTo: string }) => void;
  onClose: () => void;
}) {
  const [localPetitioner, setLocalPetitioner] = useState(petitioner);
  const [localRespondent, setLocalRespondent] = useState(respondent);
  const [localDateFrom, setLocalDateFrom] = useState(dateFrom);
  const [localDateTo, setLocalDateTo] = useState(dateTo);

  const handleApply = () => {
    onApply({ petitioner: localPetitioner, respondent: localRespondent, dateFrom: localDateFrom, dateTo: localDateTo });
  };

  const handleClear = () => {
    setLocalPetitioner(''); setLocalRespondent('');
    setLocalDateFrom(''); setLocalDateTo('');
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />

      {/* Dialog */}
      <div className="relative bg-white rounded-xl shadow-xl w-full max-w-md mx-4 p-6">
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-base font-semibold text-slate-800">Advanced Search</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 transition-colors">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider block mb-1.5">
              Petitioner
            </label>
            <PartyCombobox
              label=""
              value={localPetitioner}
              onChange={setLocalPetitioner}
              options={availablePetitioners}
            />
          </div>

          <div>
            <label className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider block mb-1.5">
              Respondent
            </label>
            <PartyCombobox
              label=""
              value={localRespondent}
              onChange={setLocalRespondent}
              options={availableRespondents}
            />
          </div>

          <div>
            <label className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider block mb-1.5">
              Date of Decision
            </label>
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1">
                <span className="text-[10px] text-slate-400">From</span>
                <input
                  type="date"
                  value={localDateFrom}
                  max={todayLocalISO()}
                  onChange={(e) => setLocalDateFrom(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm
                    focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 outline-none"
                />
              </div>
              <div className="flex flex-col gap-1">
                <span className="text-[10px] text-slate-400">To</span>
                <input
                  type="date"
                  value={localDateTo}
                  max={todayLocalISO()}
                  onChange={(e) => setLocalDateTo(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm
                    focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 outline-none"
                />
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between mt-6">
          <button
            onClick={handleClear}
            className="text-sm text-slate-400 hover:text-red-500 transition-colors"
          >
            Clear all
          </button>
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm text-slate-600 border border-slate-200 rounded-lg
                hover:bg-slate-50 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleApply}
              className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg
                hover:bg-blue-700 transition-colors"
            >
              Apply
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function FilterText({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">{label}</span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Any"
        className="w-36 px-2 py-1 bg-transparent border-none text-sm text-slate-700 focus:outline-none"
      />
    </div>
  );
}

function PartyCombobox({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
}) {
  const [inputValue, setInputValue] = useState(value);
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Sync when parent resets value (e.g. party no longer in results)
  useEffect(() => { setInputValue(value); }, [value]);

  const filtered = inputValue.trim()
    ? options.filter((o) => o.toLowerCase().includes(inputValue.trim().toLowerCase()))
    : options;

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setInputValue(e.target.value);
    if (!e.target.value) onChange(''); // clear triggers search immediately
    setIsOpen(true);
  };

  const handleSelect = (option: string) => {
    setInputValue(option);
    onChange(option); // selection triggers search
    setIsOpen(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') { onChange(inputValue); setIsOpen(false); }
    if (e.key === 'Escape') { setIsOpen(false); }
  };

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node))
        setIsOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  return (
    <div className="flex flex-col gap-0.5 relative" ref={containerRef}>
      {label && <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">{label}</span>}
      <input
        type="text"
        value={inputValue}
        onChange={handleChange}
        onFocus={() => setIsOpen(true)}
        onKeyDown={handleKeyDown}
        placeholder="Any"
        className="w-full px-2 py-1 bg-transparent border-none text-sm text-slate-700 focus:outline-none"
      />
      {isOpen && (filtered.length > 0 || inputValue.trim()) && (
        <div className="absolute top-full left-0 z-50 mt-1 w-64 max-h-48 overflow-y-auto
          bg-white border border-slate-200 rounded-lg shadow-lg">
          {filtered.map((o) => (
            <button
              key={o}
              type="button"
              onMouseDown={() => handleSelect(o)}
              className="w-full text-left px-3 py-1.5 text-sm text-slate-700 hover:bg-blue-50
                hover:text-blue-700 truncate"
            >
              {o}
            </button>
          ))}
          {inputValue.trim() && filtered.length === 0 && (
            <button
              type="button"
              onMouseDown={() => handleSelect(inputValue.trim())}
              className="w-full text-left px-3 py-1.5 text-sm text-blue-600 hover:bg-blue-50 truncate"
            >
              Search for &ldquo;{inputValue.trim()}&rdquo;
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function SectionCombobox({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
}) {
  const [input, setInput] = useState(value);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => { setInput(value); }, [value]);

  const filtered = input.trim()
    ? options.filter((o) => o.toLowerCase().includes(input.trim().toLowerCase()))
    : options;

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  return (
    <div className="relative" ref={ref}>
      <input
        type="text"
        value={input}
        placeholder="Any section"
        onChange={(e) => { setInput(e.target.value); setOpen(true); if (!e.target.value) onChange(''); }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { onChange(input); setOpen(false); }
          if (e.key === 'Escape') setOpen(false);
        }}
        className="w-32 pl-2 pr-2 py-1 bg-transparent border-none text-sm text-slate-700 focus:outline-none"
      />
      {open && filtered.length > 0 && (
        <div className="absolute top-full left-0 z-50 mt-1 w-52 max-h-56 overflow-y-auto
          bg-white border border-slate-200 rounded-lg shadow-lg">
          {filtered.slice(0, 80).map((o) => (
            <button
              key={o}
              type="button"
              onMouseDown={() => { onChange(o); setInput(o); setOpen(false); }}
              className="w-full text-left px-3 py-1.5 text-sm text-slate-700 hover:bg-blue-50 hover:text-blue-700 truncate"
            >
              {o}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
  placeholder: string;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
        {label}
      </span>
      <div className="relative">
        <select
          value={value || placeholder}
          onChange={(e) => onChange(e.target.value === placeholder ? '' : e.target.value)}
          className="appearance-none pl-3 pr-7 py-1 bg-transparent border-none text-sm text-slate-700
            focus:outline-none cursor-pointer"
        >
          {options.map((o) => (
            <option key={o}>{o}</option>
          ))}
        </select>
        <svg
          className="absolute right-0 top-1.5 w-4 h-4 text-slate-400 pointer-events-none"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={1.5}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
        </svg>
      </div>
    </div>
  );
}

export default function SearchPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center h-screen">
          <div className="w-7 h-7 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>
      }
    >
      <SearchPageContent />
    </Suspense>
  );
}
