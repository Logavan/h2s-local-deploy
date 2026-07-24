// Type declarations for CSS imports
declare module "*.css" {
  const content: string
  export default content
}

declare module "@xyflow/react/dist/style.css" {
  const content: string
  export default content
}
