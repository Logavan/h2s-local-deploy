export const textToXmlFile = (text: string, filename: string): File => {
  const blob = new Blob([text], { type: "application/xml" })
  return new File([blob], filename, { type: "application/xml" })
}
