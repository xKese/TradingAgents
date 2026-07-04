import { useState } from 'react'
import Head from 'next/head'
import AnalysisForm from '../components/AnalysisForm'
import ResultsPanel from '../components/ResultsPanel'

export default function Dashboard() {
  const [results, setResults] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleAnalysis = async (config: any) => {
    setLoading(true)
    setError(null)

    try {
      const response = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      })

      if (!response.ok) {
        throw new Error(`Analysis failed: ${response.statusText}`)
      }

      const data = await response.json()
      setResults(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred')
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <Head>
        <title>StrattonOak - Financial Analysis</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </Head>

      <div className="min-h-screen bg-gradient-to-br from-indigo-500 via-purple-500 to-pink-500">
        <div className="container mx-auto p-6">
          {/* Header */}
          <div className="text-center text-white mb-8">
            <div className="text-4xl font-bold mb-2">🚀 StrattonOak</div>
            <p className="text-lg opacity-90">Financials Agentic Analyzer</p>
          </div>

          {/* Main Layout */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Left: Configuration Panel */}
            <AnalysisForm onSubmit={handleAnalysis} loading={loading} />

            {/* Right: Results Panel */}
            <ResultsPanel
              results={results}
              loading={loading}
              error={error}
            />
          </div>
        </div>
      </div>
    </>
  )
}
