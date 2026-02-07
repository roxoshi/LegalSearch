'use client';

import { useState, useEffect, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import SearchResult from '@/components/SearchResult';
import { Header } from '@/components/Header';
import { MagnifyingGlassIcon } from '@heroicons/react/24/outline';
import { getApiUrl } from '@/lib/api';

function SearchPageContent() {
  const searchParams = useSearchParams();
  const router = useRouter();

  const [query, setQuery] = useState(searchParams.get('q') || '');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [filters, setFilters] = useState({
    court: searchParams.get('court') || '',
    isGst: searchParams.get('is_gst') || '',
    judge: searchParams.get('judge') || '',
    decisionDate: searchParams.get('decision_date') || ''
  });

  const performSearch = async (searchQuery: string, searchFilters: any) => {
    if (!searchQuery.trim()) return;

    setLoading(true);
    try {
      const apiUrl = getApiUrl();
      const params = new URLSearchParams({
        q: searchQuery,
        ...searchFilters.court && { court: searchFilters.court },
        ...searchFilters.isGst && { is_gst: searchFilters.isGst },
        ...searchFilters.judge && { judge: searchFilters.judge },
        ...searchFilters.decisionDate && { decision_date: searchFilters.decisionDate }
      });

      const response = await fetch(`${apiUrl}/search?${params.toString()}`);
      if (!response.ok) throw new Error('Search failed');
      const data = await response.json();
      setResults(data);
    } catch (error) {
      console.error("Search failed:", error);
      setResults([]);
    } finally {
      setLoading(false);
    }
  };

  // Initial search from URL
  useEffect(() => {
    const q = searchParams.get('q');
    if (q) {
      setQuery(q);
      const urlFilters = {
        court: searchParams.get('court') || '',
        isGst: searchParams.get('is_gst') || '',
        judge: searchParams.get('judge') || '',
        decisionDate: searchParams.get('decision_date') || ''
      };
      setFilters(urlFilters);
      performSearch(q, urlFilters);
    }
  }, [searchParams]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    const params = new URLSearchParams();
    if (query) params.set('q', query);
    if (filters.court) params.set('court', filters.court);
    if (filters.isGst) params.set('is_gst', filters.isGst);
    if (filters.judge) params.set('judge', filters.judge);
    if (filters.decisionDate) params.set('decision_date', filters.decisionDate);

    router.push(`/?${params.toString()}`);
  };

  return (
    <div className="min-h-screen bg-[#fcfcfc] text-slate-900 font-sans selection:bg-blue-100">
      <Header />
      {/* Search Header Section */}
      <section className={`transition-all duration-500 ease-in-out px-6 ${results.length > 0 || loading ? 'pt-8 pb-8 border-b border-slate-100 bg-white' : 'pt-[30vh]'}`}>
        <div className="max-w-7xl mx-auto">
          {(!results.length && !loading) && (
            <h1 className="text-5xl font-light tracking-tight text-center mb-12 text-slate-800">
              Legal Search <span className="font-bold text-blue-600">Buddy</span>
            </h1>
          )}
          <form onSubmit={handleSearch} className="max-w-6xl mx-auto relative group">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search legal documents using natural language..."
              className="w-full pl-6 pr-14 py-4 bg-white border border-slate-200 rounded-2xl shadow-sm
                focus:ring-4 focus:ring-blue-500/10 focus:border-blue-500 outline-none
                transition-all duration-200 text-lg"
            />
            <button
              type="submit"
              className="absolute right-3 top-3 p-2 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-colors"
            >
              <MagnifyingGlassIcon className="w-6 h-6" />
            </button>
            {loading && (
              <div className="absolute -right-12 top-4">
                <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-blue-500"></div>
              </div>
            )}
          </form>

          {/* Filters Section - Below Search Bar */}
          <div className="max-w-6xl mx-auto mt-4">
            <div className="flex flex-wrap items-center gap-4">
              <div className="flex items-center gap-2">
                <label className="text-xs font-medium text-slate-500 uppercase tracking-wide">Court</label>
                <select
                  value={filters.court}
                  onChange={(e) => setFilters({ ...filters, court: e.target.value })}
                  className="px-4 py-2 bg-slate-50 border border-slate-200 rounded-xl text-sm focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none transition-all"
                >
                  <option value="">All Courts</option>
                  <option value="Supreme Court">Supreme Court</option>
                  <option value="High Court">High Court</option>
                  <option value="District Court">District Court</option>
                  <option value="Tribunal">Tribunal</option>
                </select>
              </div>
              <div className="flex items-center gap-2">
                <label className="text-xs font-medium text-slate-500 uppercase tracking-wide">GST Case</label>
                <select
                  value={filters.isGst}
                  onChange={(e) => setFilters({ ...filters, isGst: e.target.value })}
                  className="px-4 py-2 bg-slate-50 border border-slate-200 rounded-xl text-sm focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none transition-all"
                >
                  <option value="">All</option>
                  <option value="yes">Yes</option>
                  <option value="no">No</option>
                </select>
              </div>
              <div className="flex items-center gap-2">
                <label className="text-xs font-medium text-slate-500 uppercase tracking-wide">Judge</label>
                <input
                  type="text"
                  placeholder="Judge name"
                  value={filters.judge}
                  onChange={(e) => setFilters({ ...filters, judge: e.target.value })}
                  className="px-4 py-2 bg-slate-50 border border-slate-200 rounded-xl text-sm focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none transition-all"
                />
              </div>
              <div className="flex items-center gap-2">
                <label className="text-xs font-medium text-slate-500 uppercase tracking-wide">Decision Date</label>
                <input
                  type="date"
                  value={filters.decisionDate}
                  onChange={(e) => setFilters({ ...filters, decisionDate: e.target.value })}
                  className="px-4 py-2 bg-slate-50 border border-slate-200 rounded-xl text-sm focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none transition-all"
                />
              </div>
              <button
                onClick={handleSearch}
                className="px-4 py-2 bg-slate-900 text-white rounded-xl text-sm font-medium hover:bg-slate-800 transition-colors shadow-sm"
              >
                Apply Filters
              </button>
            </div>
          </div>
        </div>
      </section>

      <main className="max-w-7xl mx-auto px-6 py-12">
        {/* Results Area */}
        <div className="max-w-6xl mx-auto">
          {results.length > 0 ? (
            <div className="space-y-6">
              <p className="text-sm text-slate-500 mb-6">Found {results.length} relevant documents</p>
              {results.map((result: any, index) => (
                <div
                  key={result.id}
                  className="animate-in fade-in slide-in-from-bottom-4 duration-500"
                  style={{ animationDelay: `${index * 50}ms` }}
                >
                  <SearchResult result={result} />
                </div>
              ))}
            </div>
          ) : !loading && query && (
            <div className="text-center py-20 bg-white rounded-3xl border border-slate-100 border-dashed">
              <p className="text-slate-400">No documents found matching your criteria.</p>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

export default function SearchPage() {
  return (
    <Suspense fallback={<div>Loading...</div>}>
      <SearchPageContent />
    </Suspense>
  );
}
