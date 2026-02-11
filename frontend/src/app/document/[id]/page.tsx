'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ArrowLeftIcon, SparklesIcon } from '@heroicons/react/24/outline';
import { getApiUrl } from '@/lib/api';

export default function DocumentDetailsPage() {
  const { id } = useParams();
  const router = useRouter();
  const [doc, setDoc] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState<string | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
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

  const generateSummary = async () => {
    setSummaryLoading(true);
    try {
      const res = await fetch(`${apiUrl}/document/${id}/summary`);
      if (!res.ok) throw new Error('Failed to generate summary');
      const data = await res.json();
      setSummary(data.summary);
    } catch (err) {
      console.error('Failed to generate summary', err);
      setSummary('Failed to generate summary. Please try again.');
    } finally {
      setSummaryLoading(false);
    }
  };

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
            <div className="prose prose-slate max-w-none">
              <h3 className="text-lg font-semibold text-slate-800 mb-4 uppercase tracking-wider">Judgment Content</h3>
              <div className="bg-white p-8 rounded-2xl border border-slate-100 shadow-sm leading-relaxed text-slate-700">
                {doc.display_content ? (
                  <div
                    className="judgment-content"
                    dangerouslySetInnerHTML={{ __html: doc.display_content }}
                  />
                ) : (
                  <div className="whitespace-pre-wrap">
                    {doc.content}
                  </div>
                )}
              </div>
            </div>
          </div>

          <aside className="space-y-6">
            {/* AI Summary Section */}
            <div className="bg-gradient-to-br from-blue-50 to-indigo-50 p-6 rounded-2xl border border-blue-100">
              <h3 className="font-semibold text-slate-900 mb-4 flex items-center gap-2">
                <SparklesIcon className="w-5 h-5 text-blue-600" />
                AI Summary
              </h3>
              {summary ? (
                <div className="text-sm text-slate-700 leading-relaxed">
                  {summary}
                </div>
              ) : (
                <div>
                  <p className="text-sm text-slate-600 mb-4">
                    Generate an AI-powered summary of this case to quickly understand the key points.
                  </p>
                  <button
                    onClick={generateSummary}
                    disabled={summaryLoading}
                    className="w-full py-2 px-4 bg-blue-600 text-white rounded-xl text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                  >
                    {summaryLoading ? (
                      <>
                        <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white"></div>
                        Generating...
                      </>
                    ) : (
                      <>
                        <SparklesIcon className="w-4 h-4" />
                        Generate Summary
                      </>
                    )}
                  </button>
                </div>
              )}
            </div>

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
          </aside>
        </div>
      </div>
    </div>
  );
}
