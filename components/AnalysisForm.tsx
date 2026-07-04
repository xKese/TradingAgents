import { useState } from 'react'

interface AnalysisFormProps {
  onSubmit: (config: any) => Promise<void>
  loading: boolean
}

export default function AnalysisForm({ onSubmit, loading }: AnalysisFormProps) {
  const [ticker, setTicker] = useState('')
  const [date, setDate] = useState(new Date().toISOString().split('T')[0])
  const [provider, setProvider] = useState('anthropic')
  const [deepModel, setDeepModel] = useState('auto')
  const [analysts, setAnalysts] = useState({
    market: true,
    sentiment: false,
    news: true,
    fundamentals: false,
  })

  const handleAnalystChange = (analyst: string) => {
    setAnalysts((prev) => ({
      ...prev,
      [analyst]: !prev[analyst as keyof typeof analysts],
    }))
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!ticker.trim()) {
      alert('Please enter a stock ticker')
      return
    }

    await onSubmit({
      ticker: ticker.toUpperCase(),
      date,
      provider,
      deepModel,
      analysts: Object.entries(analysts)
        .filter(([_, v]) => v)
        .map(([k]) => k),
    })
  }

  return (
    <div className="bg-white rounded-lg shadow-lg p-6 h-fit">
      <h2 className="text-2xl font-bold text-gray-800 mb-6">
        Analysis Configuration
      </h2>

      <form onSubmit={handleSubmit} className="space-y-4">
        {/* Stock Ticker */}
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-1">
            Stock Ticker <span className="text-red-500">*</span>
          </label>
          <input
            type="text"
            placeholder="e.g., AAPL, NVDA, 0700.HK"
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            disabled={loading}
          />
        </div>

        {/* Analysis Date */}
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-1">
            Analysis Date <span className="text-red-500">*</span>
          </label>
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            disabled={loading}
          />
        </div>

        {/* LLM Provider */}
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-1">
            LLM Provider
          </label>
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            disabled={loading}
          >
            <option value="anthropic">Anthropic Claude / key set</option>
            <option value="openai">OpenAI GPT-4</option>
            <option value="google">Google Gemini</option>
          </select>
        </div>

        {/* Deep Thinking Model */}
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-1">
            Deep Thinking Model
          </label>
          <select
            value={deepModel}
            onChange={(e) => setDeepModel(e.target.value)}
            className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            disabled={loading}
          >
            <option value="auto">Auto (from ENV)</option>
            <option value="claude-opus">Claude Opus</option>
            <option value="gpt-4">GPT-4 Turbo</option>
          </select>
        </div>

        {/* Select Analysts */}
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-3">
            Select Analysts
          </label>
          <p className="text-xs text-gray-500 mb-3">
            Fewer analysts = fewer LLM calls. Add more for a deeper analysis.
          </p>

          <div className="space-y-2">
            {[
              {
                key: 'market',
                label: 'Market Analyst',
                desc: 'Technical indicators and price patterns',
              },
              {
                key: 'sentiment',
                label: 'Sentiment Analyst',
                desc: 'StockTwits, Reddit sentiment',
              },
              {
                key: 'news',
                label: 'News Analyst',
                desc: 'News and macroeconomic impact',
              },
              {
                key: 'fundamentals',
                label: 'Fundamentals Analyst',
                desc: 'Financial statements and metrics',
              },
            ].map(({ key, label, desc }) => (
              <label
                key={key}
                className="flex items-start p-2 hover:bg-gray-50 rounded cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={analysts[key as keyof typeof analysts]}
                  onChange={() => handleAnalystChange(key)}
                  className="mt-1 w-4 h-4 accent-blue-600"
                  disabled={loading}
                />
                <div className="ml-3">
                  <div className="font-medium text-gray-700">{label}</div>
                  <div className="text-xs text-gray-500">{desc}</div>
                </div>
              </label>
            ))}
          </div>
        </div>

        {/* Advanced Options */}
        <details className="group pt-2 border-t">
          <summary className="cursor-pointer font-semibold text-gray-700 flex items-center">
            Advanced Options
            <span className="ml-2 text-lg">▶</span>
          </summary>
          <div className="mt-3 text-sm text-gray-600">
            <p>Temperature, debate rounds, and other parameters coming soon.</p>
          </div>
        </details>

        {/* Submit Button */}
        <button
          type="submit"
          disabled={loading}
          className="w-full mt-6 py-3 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-400 text-white font-bold rounded-lg transition"
        >
          {loading ? 'Analyzing...' : 'Start Analysis'}
        </button>
      </form>
    </div>
  )
}
