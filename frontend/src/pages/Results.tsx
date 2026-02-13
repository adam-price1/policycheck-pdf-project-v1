import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { listDocuments } from "../api/documents";
import type { Document } from "../types";

export default function Results() {
  const navigate = useNavigate();
  const [docs, setDocs] = useState<Document[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    listDocuments({ limit: 100 })
      .then((resp) => {
        setDocs(resp?.items ?? resp?.documents ?? []);
        setTotal(resp?.total ?? 0);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="text-gray-500">Loading results...</div>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900 mb-1">Crawl Results</h1>
        <p className="text-gray-600">{total} documents discovered</p>
      </div>

      <div className="mb-6 flex gap-3">
        <button
          onClick={() => navigate("/review")}
          className="px-4 py-2 bg-primary-600 text-white rounded-lg font-medium hover:bg-primary-700"
        >
          Review Documents
        </button>
        <button
          onClick={() => navigate("/library")}
          className="px-4 py-2 border border-gray-300 text-gray-700 rounded-lg font-medium hover:bg-gray-50"
        >
          View Library
        </button>
      </div>

      {docs.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-white py-16 text-center">
          <p className="text-gray-500">No documents found.</p>
        </div>
      ) : (
        <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {docs.map((doc) => (
            <div key={doc.id} className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm hover:shadow-lg transition-all">
              <div className="mb-3 flex items-start justify-between">
                <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ${
                  doc.status === "validated" ? "bg-green-100 text-green-700" : "bg-amber-100 text-amber-700"
                }`}>
                  {doc.status}
                </span>
                <span className="text-xs text-gray-400">{doc.country}</span>
              </div>
              <h3 className="mb-2 text-sm font-semibold text-gray-900 line-clamp-2">{doc.insurer}</h3>
              <div className="mb-3 space-y-1">
                <p className="text-xs text-gray-500"><span className="font-medium">Type:</span> {doc.policy_type}</p>
                <p className="text-xs text-gray-500"><span className="font-medium">Classification:</span> {doc.classification}</p>
                <p className="text-xs text-gray-500"><span className="font-medium">Confidence:</span> {((doc.confidence ?? 0) * 100).toFixed(0)}%</p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
