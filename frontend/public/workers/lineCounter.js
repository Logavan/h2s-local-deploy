// Web Worker for counting lines
self.onmessage = (e) => {
  const text = e.data
  if (!text) {
    self.postMessage(0)
    return
  }

  // Process text in chunks to avoid blocking
  const chunkSize = 5000
  const lines = text.split("\n")
  let nonEmptyCount = 0

  for (let i = 0; i < lines.length; i += chunkSize) {
    const chunk = lines.slice(i, i + chunkSize)
    nonEmptyCount += chunk.filter((line) => line.trim().length > 0).length
  }

  self.postMessage(nonEmptyCount)
}
