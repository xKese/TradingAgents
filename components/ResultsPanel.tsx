import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'

interface ResultsPanelProps {
  results: any
  loading: boolean
  error: string | null
}

export default function ResultsPanel({ results, loading, error }: ResultsPanelProps) {
  return (
    <div className="bg-white rounded-lg shadow-lg p-6">
      {error && (
        <div className="text-center py-16">
          <div className="text-6xl mb-4">⚠️</div>
          <h3 className="text-lg font-semibold text-red-600 mb-2">Error</h3>
          <p className="text-gray-600">{error}</p>
        </div>
      )}

      {loading && (
        <div className="text-center py-16">
          <div className="animate-spin text-4xl mb-4">📊</div>
          <h3 className="text-lg font-semibold text-gray-700 mb-2">
            Analyzing...
          </h3>
          <p className="text-gray-600">
            Real-time updates will appear here as agents work.
          </p>
        </div>
      )}

      {!loading && !results && !error && (
        <div className="text-center py-16">
          <div className="text-6xl mb-4">📊</div>
          <h3 className="text-lg font-semibold text-gray-700 mb-2">
            Ready to Analyze
          </h3>
          <p className="text-gray-600">
            Configure your analysis on the left and click "Start Analysis"
          </p>
        </div>
      )}

      {results && (
        <div className="space-y-6">
          {/* Summary */}
          <div className="bg-gradient-to-r from-blue-50 to-indigo-50 p-4 rounded-lg border border-blue-200">
            <h3 className="font-bold text-gray-800 mb-2">Analysis Summary</h3>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <span className="text-gray-600">Ticker:</span>{' '}
                <span className="font-semibold">{results.ticker}</span>
              </div>
              <div>
                <span className="text-gray-600">Date:</span>{' '}
                <span className="font-semibold">{results.date}</span>
              </div>
              {results.recommendation && (
                <div className="col-span-2">
                  <span className="text-gray-600">Recommendation:</span>{' '}
                  <span
                    className={`font-bold px-2 py-1 rounded ${
                      results.recommendation.toUpperCase() === 'BUY'
                        ? 'bg-green-100 text-green-800'
                        : results.recommendation.toUpperCase() === 'SELL'
                        ? 'bg-red-100 text-red-800'
                        : 'bg-yellow-100 text-yellow-800'
                    }`}
                  >
                    {results.recommendation}
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Analysis Breakdown */}
          {results.analysts && (
            <div>
              <h3 className="font-bold text-gray-800 mb-3">Agent Reports</h3>
              <div className="space-y-3">
                {Object.entries(results.analysts).map(([analyst, analysis]: any) => (
                  <div key={analyst} className="border-l-4 border-blue-500 pl-4 py-2">
                    <h4 className="font-semibold text-gray-700 capitalize mb-1">
                      {analyst.replace(/_/g, ' ')}
                    </h4>
                    <p className="text-gray-600 text-sm">{analysis.summary || analysis}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Mock Chart */}
          {results.metrics && (
            <div>
              <h3 className="font-bold text-gray-800 mb-3">Performance Metrics</h3>
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={results.metrics}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="name" />
                  <YAxis />
                  <Tooltip />
                  <Legend />
                  <Bar dataKey="value" fill="#6366f1" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Raw Data */}
          <details className="group">
            <summary className="cursor-pointer font-semibold text-gray-700">
              Full Analysis Data
            </summary>
            <pre className="mt-3 bg-gray-50 p-3 rounded text-xs overflow-auto max-h-64 text-gray-700">
              {JSON.stringify(results, null, 2)}
            </pre>
          </details>
        </div>
      )}
    </div>
  )
}
