import type { NextApiRequest, NextApiResponse } from 'next'
import axios from 'axios'

const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8000'

export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse
) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' })
  }

  try {
    const { ticker, date, provider, deepModel, analysts } = req.body

    // Validate input
    if (!ticker || !date) {
      return res.status(400).json({ error: 'Missing required fields' })
    }

    // Call backend API
    // TODO: Replace with actual backend endpoint
    const response = await axios.post(`${BACKEND_URL}/api/analyze`, {
      ticker,
      date,
      provider,
      deepModel,
      analysts,
    })

    res.status(200).json(response.data)
  } catch (error) {
    console.error('Analysis error:', error)
    res.status(500).json({
      error: error instanceof Error ? error.message : 'Analysis failed',
    })
  }
}
