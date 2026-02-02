import Link from 'next/link';

export default function SearchResult({ result }: { result: any }) {
  return (
    <Link href={`/document/${result.id}`} className="block">
      <div className="group p-6 bg-white border border-slate-100 rounded-2xl transition-all duration-200 
      hover:border-blue-200 hover:shadow-lg hover:shadow-blue-500/5 cursor-pointer">
        <div className="flex justify-between items-start gap-4">
          <div className="flex-1">
            <p className="text-xl font-semibold text-blue-600 mb-1 group-hover:underline transition-all">
              {result.title || "Untitled Document"}
            </p>
            <div className="flex gap-3 text-xs text-slate-500 mb-2 uppercase tracking-wide">
              <span className="font-semibold">{result.case_id}</span>
              <span>•</span>
              <span>{result.citation}</span>
            </div>
            <p className="mt-2 text-slate-600 leading-relaxed line-clamp-2">
              {result.content || "This document contains information regarding your search"}
            </p>
          </div>
          <div className="flex flex-col items-end">
            <div className="px-3 py-1 rounded-full bg-slate-50 text-[10px] font-bold text-slate-500 border border-slate-100">
              MATCH: {(result.rrf_score * 100).toFixed(1)}%
            </div>
          </div>
        </div>
        <div className="mt-4 flex gap-2">
          <span className="text-[11px] bg-slate-100 text-slate-600 px-2 py-0.5 rounded">#metadata</span>
          <span className="text-[11px] bg-slate-100 text-slate-600 px-2 py-0.5 rounded">#vector-search</span>

        </div>

      </div>
    </Link>
  );
}
