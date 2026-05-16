'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ArrowLeftIcon, DocumentArrowDownIcon } from '@heroicons/react/24/outline';
import { getApiUrl } from '@/lib/api';

export default function DocumentDetailsPage() {
  const { id } = useParams();
  const router = useRouter();
  const [doc, setDoc] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [apiUrl, setApiUrl] = useState('');

  useEffect(() => {
    setApiUrl(getApiUrl());
  }, []);

  useEffect(() => {
    const fetchDoc = async () => {
      if (!id || !apiUrl) return;
      try {
        const res = await fetch(`${apiUrl}/document/${id}`);
        if (!res.ok) throw new Error('Document not found');
        const data = await res.json();
        setDoc(data);
      } catch (err) {
        console.error("Failed to load document", err);
      } finally {
        setLoading(false);
      }
    };
    fetchDoc();
  }, [id, apiUrl]);

  if (loading) return (
    <div className="flex justify-center items-center min-h-screen">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
    </div>
  );

  if (!doc) return <div className="p-20 text-center text-slate-500">Document not found.</div>;

  return (
    <div className="min-h-screen bg-[#fcfcfc] text-slate-900 font-sans">
      <div className="max-w-7xl mx-auto py-12 px-6">
        <button
          onClick={() => router.back()}
          className="flex items-center text-slate-500 hover:text-blue-600 mb-8 transition-colors"
        >
          <ArrowLeftIcon className="w-4 h-4 mr-2" />
          Back to Search
        </button>

        <header className="mb-10 border-b border-slate-200 pb-8">
          <h1 className="text-3xl font-serif font-medium text-slate-900 mb-4 leading-tight">
            {doc.title}
          </h1>
          <div className="flex flex-wrap gap-4 text-sm text-slate-600">
            <div className="flex items-center bg-slate-100 px-3 py-1 rounded-full">
              <span className="font-semibold mr-2">Case ID:</span> {doc.case_id}
            </div>
            <div className="flex items-center bg-slate-100 px-3 py-1 rounded-full">
              <span className="font-semibold mr-2">Date:</span> {doc.decision_date}
            </div>
            <div className="flex items-center bg-slate-100 px-3 py-1 rounded-full">
              <span className="font-semibold mr-2">Court:</span> {doc.court}
            </div>
          </div>
        </header>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-12">
          <div className="lg:col-span-2">
            <div className="bg-white p-8 rounded-2xl border border-slate-100 shadow-sm leading-relaxed text-slate-700 text-sm">
              <style>{`
                .judgment-content h2 {
                  font-size: 1.1rem;
                  font-weight: 700;
                  color: #1e293b;
                  margin-top: 1.75rem;
                  margin-bottom: 0.5rem;
                }
                .judgment-content h2:first-child {
                  margin-top: 0;
                }
                .judgment-content p {
                  margin-bottom: 0.75rem;
                }
              `}</style>
              {doc.display_content ? (
                <div
                  className="judgment-content"
                  dangerouslySetInnerHTML={{ __html: doc.display_content }}
                />
              ) : (
                <div className="whitespace-pre-wrap">{doc.content}</div>
              )}
            </div>
          </div>

          <aside className="space-y-6">
            {/* Case Metadata */}
            <div className="bg-slate-50 p-6 rounded-2xl border border-slate-100">
              <h3 className="font-semibold text-slate-900 mb-4">Case Metadata</h3>
              <div className="space-y-4 text-sm">
                <div>
                  <p className="text-slate-500 text-xs uppercase tracking-wide mb-1">Judge</p>
                  <p className="font-medium">{doc.judge}</p>
                </div>
                <div>
                  <p className="text-slate-500 text-xs uppercase tracking-wide mb-1">Petitioner</p>
                  <p className="font-medium">{doc.petitioner}</p>
                </div>
                <div>
                  <p className="text-slate-500 text-xs uppercase tracking-wide mb-1">Respondent</p>
                  <p className="font-medium">{doc.respondent}</p>
                </div>
                <div>
                  <p className="text-slate-500 text-xs uppercase tracking-wide mb-1">Citation</p>
                  <p className="font-medium">{doc.citation}</p>
                </div>
              </div>
            </div>

            {/* Original Judgment Download */}
            <div className="bg-slate-50 p-6 rounded-2xl border border-slate-100">
              <h3 className="font-semibold text-slate-900 mb-3">Original Judgment</h3>
              <a
                href={`${apiUrl}/document/${id}/pdf`}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center justify-center gap-2 w-full py-2 px-4 bg-slate-800 text-white rounded-xl text-sm font-medium hover:bg-slate-900 transition-colors"
              >
                <DocumentArrowDownIcon className="w-4 h-4" />
                Download PDF
              </a>
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}
