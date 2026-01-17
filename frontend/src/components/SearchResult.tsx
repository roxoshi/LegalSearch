export default function SearchResult({ result }: { result: any }) {
  return (
    <div className="group p-6 bg-white border border-slate-100 rounded-2xl transition-all duration-200 
      hover:border-blue-200 hover:shadow-lg hover:shadow-blue-500/5">
      <div className="flex justify-between items-start gap-4">
        <div className="flex-1">
          <p className="text-xl font-semibold uppercase tracking-wider text-blue-500 mb-1">
            Document {result.id}
          </p>
          <h3 className="text-xl font-medium text-slate-900 group-hover:text-blue-600 transition-colors cursor-pointer">
            {result.title || "Untitled Document"}
          </h3>
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
  );
}
