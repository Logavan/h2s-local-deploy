import { spawn } from "child_process"
import { PYTHON_SCRIPTS } from "./config"

/**
 * Executes a Python script with the given input and returns the result
 * @param scriptName The name of the script in the PYTHON_SCRIPTS config
 * @param input The input to pass to the script
 * @returns The result of the script execution
 */
export async function executePythonScript(scriptName: keyof typeof PYTHON_SCRIPTS, input: string): Promise<any> {
  return new Promise((resolve, reject) => {
    const scriptPath = PYTHON_SCRIPTS[scriptName]
    const pythonProcess = spawn("python", [scriptPath])

    let result = ""
    let error = ""

    pythonProcess.stdout.on("data", (data) => {
      result += data.toString()
    })

    pythonProcess.stderr.on("data", (data) => {
      error += data.toString()
    })

    pythonProcess.on("close", (code) => {
      if (code !== 0 || error) {
        reject(new Error(error || `Process exited with code ${code}`))
      } else {
        try {
          resolve(JSON.parse(result))
        } catch (e) {
          resolve(result)
        }
      }
    })

    pythonProcess.stdin.write(input)
    pythonProcess.stdin.end()
  })
}
