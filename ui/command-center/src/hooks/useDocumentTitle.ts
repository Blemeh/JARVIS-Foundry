import { useEffect } from "react"

export const useDocumentTitle = (title: string) => {
  useEffect(() => {
    const previous = document.title
    document.title = `${title} · JARVIS Command Center`
    return () => {
      document.title = previous
    }
  }, [title])
}
