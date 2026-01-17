'use client';

import { useState } from 'react';
import SearchResult from '@/components/SearchResult';
import { MagnifyingGlassIcon } from '@heroicons/react/24/outline';

export default function SearchPage() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    
    try {
      // Points to your FastAPI backend
      const response = await fetch(`http://localhost:8000/search?q=${encodeURIComponent(query)}`);
      const data = await response.json();
      setResults(data);
    } catch (error) {
      console.error("Search failed:", error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#fcfcfc] text-slate-900 font-sans selection:bg-blue-100">
      {/* Search Header Section */}
      <section className={`transition-all duration-500 ease-in-out ${results.length > 0 ? 'pt-12 pb-8' : 'pt-[30vh]'}`}>
        <div className="max-w-3xl mx-auto px-6">
          {!results.length && (
            <h1 className="text-4xl font-light tracking-tight text-center mb-8 text-slate-800">
              Find <span className="font-semibold text-blue-600">Anything.</span>
            </h1>
          )}
          <form onSubmit={handleSearch} className="relative group">
            <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
            </div>
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search documents using natural language..."
              className="w-full pl-12 pr-4 py-4 bg-white border border-slate-200 rounded-2xl shadow-sm
                focus:ring-4 focus:ring-blue-500/10 focus:border-blue-500 outline-none
                transition-all duration-200 text-lg"
            />
            
            {loading && (
              <div className="absolute right-4 top-4">
                <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-blue-500"></div>
              </div>
            )}
          </form>

        </div>
      </section>
      
      { /* Results Section */ }
      <section className="max-w-3xl mx-auto px-6 pb-20">
        <div className="space-y-6">
          {results.map((result: any, index) => (
            <div
              key={result.id}
              className="animate-in fade-in slide-in-from-bottom-4 duration-500"
              style={{ animationDelay: `{index * 50}ms` }}
            >
              <SearchResult result={result} />
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
