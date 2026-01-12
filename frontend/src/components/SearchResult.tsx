export default function SearchResult({ result }: { result: any }) {
  return (
    <div className="p-6 border rounded-xl hover:shadow-md transition bg-white">
      <div className="flex justify-between items-start mb-2">
        <h3 className="text-xl font-bold text-blue-800">Document #{result.id}</h3>
        <span className="text-xs font-mono bg-gray-100 px-2 py-1 rounded">
          Score: {result.rrf_score.toFixed(4)}
        </span>
      </div>
      <p className="text-gray-600 line-clamp-3">
        {/* In a real app, you would fetch and display the document summary here */}
        Click to view the full contents of this document...
      </p>
    </div>
  );
}