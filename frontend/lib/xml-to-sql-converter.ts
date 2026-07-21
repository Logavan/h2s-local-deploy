import { XMLParser } from "fast-xml-parser"

export async function convertXmlToSql(xmlData: string): Promise<string> {
  try {
    const parser = new XMLParser({
      ignoreAttributes: false,
      attributeNamePrefix: "",
      allowBooleanAttributes: true,
      parseTagValue: true,
      parseAttributeValue: true,
      trimValues: true,
    })

    const jsonData = parser.parse(xmlData)

    if (!jsonData) {
      throw new Error("Failed to parse XML data.")
    }

    // Basic SQL generation logic (can be improved based on XML structure)
    let sql = ""

    const processObject = (obj: any, tableName: string, parentKey: string | null = null) => {
      if (typeof obj === "object" && obj !== null) {
        if (Array.isArray(obj)) {
          obj.forEach((item, index) => {
            processObject(item, tableName, parentKey ? `${parentKey}_${index}` : String(index))
          })
        } else {
          const columns: string[] = []
          const values: any[] = []

          for (const key in obj) {
            if (Object.hasOwn(obj, key)) {
              const value = obj[key]

              if (typeof value === "object" && value !== null) {
                const newTableName = tableName ? `${tableName}_${key}` : key
                processObject(value, newTableName, key) // Recursive call for nested objects
              } else {
                columns.push(key)
                values.push(value)
              }
            }
          }

          if (columns.length > 0) {
            const columnNames = columns.map((col) => `\`${col}\``).join(", ")
            const valuePlaceholders = values.map(() => "?").join(", ")
            const insertStatement = `INSERT INTO \`${tableName || "root"}\` (${columnNames}) VALUES (${valuePlaceholders});`
            sql += insertStatement + "\n"
          }
        }
      }
    }

    // Determine the root element name for the main table
    const rootKey = Object.keys(jsonData)[0]
    processObject(jsonData[rootKey], rootKey)

    return sql
  } catch (error: any) {
    console.error("Error converting XML to SQL:", error)
    throw new Error(`XML to SQL conversion failed: ${error.message}`)
  }
}
