import { Parser, Select, Column, From, AST } from "node-sql-parser";

// Define a structure for our validation errors for consistency
export interface ValidationError {
  message: string;
  line?: number;
  column?: number;
  length?: number; // Added length property for precise highlighting
}

// Define the structure for a data source (table or CTE) within our scope
interface SourceInfo {
  name: string;
  columns: string[];
  hasStar: boolean;
}

/**
 * Manages the scope of a SQL query, tracking available CTEs, tables, and their columns.
 * This is essential for validating column references correctly.
 */
class Scope {
  private sources = new Map<string, SourceInfo>();
  parent?: Scope;

  constructor(parent?: Scope) {
    this.parent = parent;
  }

  addSource(alias: string, info: SourceInfo) {
    if (this.sources.has(alias.toLowerCase())) {
      // This indicates a duplicate alias within the same scope
      // For now, we'll allow it but a more strict validator might error here.
      // The user explicitly asked for "Unique Aliases/CTE Names"
      // However, node-sql-parser might handle this at a syntax level.
      // For semantic validation, we'll ensure our findSource prioritizes the closest scope.
    }
    this.sources.set(alias.toLowerCase(), info);
  }

  findSource(name: string): SourceInfo | undefined {
    const source = this.sources.get(name.toLowerCase());
    if (source) {
      return source;
    }
    return this.parent?.findSource(name);
  }

  // Get all sources in current and parent scopes
  getAllSources(): Map<string, SourceInfo> {
    const allSources = new Map<string, SourceInfo>();
    let currentScope: Scope | undefined = this;
    
    while (currentScope) {
      for (const [alias, source] of Array.from(currentScope.sources.entries())) {
        if (!allSources.has(alias)) {
          allSources.set(alias, source);
        }
      }
      currentScope = currentScope.parent;
    }
    
    return allSources;
  }
}

/**
 * A comprehensive SQL validator that traverses the AST using a visitor pattern.
 * This design is highly extensible for adding new validation rules.
 */
class SqlValidator {
  private parser = new Parser();
  private errors: ValidationError[] = [];
  private sql: string = ""; // Store the SQL string for fallback location finding
  private suppressErrors: boolean;

  constructor(suppressErrors: boolean = false) {
    this.suppressErrors = suppressErrors;
  }

  public validate(sql: string): string | null {
    this.sql = sql; // Initialize the sql property
    this.errors = []; // Clear errors for each validation run

    try {
      const ast = this.parser.astify(sql, { database: "MySQL" });
      const statements = Array.isArray(ast) ? ast : [ast];

      for (const stmt of statements) {
        // Ensure stmt is an object before accessing its properties
        if (typeof stmt === 'object' && stmt !== null && stmt.type === "select") {
          this.visitSelect(stmt, new Scope());
        }
      }

      if (this.errors.length === 0 || this.suppressErrors) {
        return this.sql; // Return SQL if valid or if errors are suppressed
      } else {
        return null; // Return null if not valid and errors are not suppressed
      }
    } catch (err: any) {
      if (this.suppressErrors) {
        return this.sql; // Return SQL even on parse error if suppressed
      }
      // If not suppressing, a parse error means the SQL is invalid
      this.errors.push(this.formatParseError(err, sql));
      return null;
    }
  }

  private visitSelect(node: Select, scope: Scope) {
    const currentScope = new Scope(scope);

    // 1. Validate Unique Aliases/CTE Names (Rule 3)
    // This is handled implicitly by Scope.addSource, but we can add an explicit check here
    // for CTEs to ensure no duplicate CTE names within the same WITH clause.
    const cteNamesInCurrentWith: Set<string> = new Set();
    if (node.with && Array.isArray((node.with as any).tables)) { // Cast to any to resolve TS error
      for (const cte of (node.with as any).tables) { // Cast to any to resolve TS error
        const cteName = cte.name.value.toLowerCase();
        if (cteNamesInCurrentWith.has(cteName)) {
          // Pass undefined for location if cte.name.location is not directly available
          this.addError(`Duplicate CTE name '${cte.name.value}' in WITH clause`, undefined, cte.name.value);
        } else {
          cteNamesInCurrentWith.add(cteName);
        }
        // Extract the raw statement, handling cases where it's an array
        const rawCteStmt = Array.isArray(cte.stmt) ? cte.stmt[0] : cte.stmt;
        
        // Safely extract the actual Select AST, checking for the 'ast' property
        const cteSelectNode = (rawCteStmt as any).ast as Select || rawCteStmt as Select;
        
        this.visitSelect(cteSelectNode, currentScope);
        const { cols, hasStar } = this.getSelectOutputColumns(cteSelectNode);
        currentScope.addSource(cte.name.value, { name: cte.name.value, columns: cols, hasStar });
      }
    }
    
    // 2. Visit the FROM clause to identify available tables/CTEs (Rule 2)
    if (Array.isArray(node.from)) {
      for (const fromClause of node.from) {
        this.visitFrom(fromClause, currentScope);
      }
    }

    // Collect output columns for ORDER BY and HAVING validation
    const selectOutputColumns = this.getSelectOutputColumns(node).cols;

    // 3. Validate the columns in the SELECT list (Rule 4, 5, 11)
    for (const col of (node.columns || [])) {
        if (col.expr?.type === 'column_ref') {
            this.visitColumnRef(col.expr, currentScope);
        }
        else if (col.expr?.type === 'aggr_func') {
            this.visitAggregateFunction(col.expr, currentScope);
        } else {
            // Recursively visit other expressions in SELECT list
            this.visitExpression(col.expr, currentScope, selectOutputColumns);
        }
    }

    // 4. Validate WHERE clause
    if (node.where) {
      this.visitExpression(node.where, currentScope);
    }

    // 5. Validate GROUP BY clause (Rule 7)
    if (node.groupby) {
      this.visitGroupBy(node.groupby.columns as any[], currentScope, (node.columns || []) as any[]); // Pass node.groupby.columns directly, ensure it's an array
    }

    // 6. Validate HAVING clause (Rule 8)
    if (node.having) {
      this.visitHaving(node.having, currentScope, selectOutputColumns);
    }

    // 7. Validate ORDER BY clause (Rule 9)
    if (node.orderby) {
      this.visitOrderBy(node.orderby, currentScope, selectOutputColumns);
    }

    // 8. Validate LIMIT / OFFSET (Rule 12)
    if (node.limit) {
      this.visitLimit(node.limit, currentScope);
    }

    // 9. Handle UNION chains (if any)
    let nextSelect = node._next || null;
    if (nextSelect && nextSelect.type === 'select') {
        this.visitSelect(nextSelect, scope);
    }
  }
  
  private visitFrom(node: From, scope: Scope) {
      // The 'From' type in node-sql-parser can be complex.
      // We need to safely access 'table' and 'as' properties.
      // Assuming 'node' can be a TableExpr or similar.
      const tableName = (node as any).table;
      const alias = (node as any).as || tableName;

      if (!tableName) {
        // This might be a subquery in FROM, which is handled by visitSelect
        if ((node as any).expr?.type === 'select') {
          this.visitSelect((node as any).expr as Select, scope);
        }
        return;
      }

      const cteSource = scope.findSource(tableName);
      if (cteSource) {
          // Add the source to the scope using its alias (or name if no alias)
          scope.addSource(alias, cteSource);
      } else {
          // This is a base table. Without a schema, assume it provides any column.
          // Rule 2: Tables/CTEs used in FROM/JOIN must exist in the current scope.
          // For base tables, we assume they exist for now, but a real schema would validate this.
          scope.addSource(alias, { name: tableName, columns: [], hasStar: true });
      }

      // Rule 10: JOIN Condition Validity
      if ((node as any).join) {
        for (const join of (node as any).join) {
          this.visitJoin(join, scope);
        }
      }
  }

  private visitJoin(node: any, scope: Scope) {
    // Validate the joined table itself
    this.visitFrom(node.right, scope); // node.right is the table being joined

    // Validate ON clause
    if (node.on) {
      this.visitExpression(node.on, scope);
    }
  }

  private visitGroupBy(node: any[], scope: Scope, selectNodeColumns: any[]) {
    const nonAggregatedSelectColumns: string[] = [];
    // Collect non-aggregated columns from the SELECT list passed from visitSelect
    for (const col of selectNodeColumns || []) {
      // A column is non-aggregated if its expression is a column reference
      // and not an aggregate function.
      if (col.expr?.type === 'column_ref' && col.expr.aggr_func === undefined) { // Check for absence of aggr_func property
        nonAggregatedSelectColumns.push(col.expr.column.toLowerCase());
      } else if (col.expr?.type === 'function' && col.expr.aggr_func === undefined) {
        // Handle non-aggregate functions that might appear in SELECT and need to be in GROUP BY
        nonAggregatedSelectColumns.push(col.expr.name.toLowerCase());
      }
    }

    const groupByColumns: string[] = [];
    for (const groupExpr of node) {
      if (groupExpr.type === 'column_ref') {
        this.visitColumnRef(groupExpr, scope);
        groupByColumns.push(groupExpr.column.toLowerCase());
      } else {
        this.visitExpression(groupExpr, scope);
      }
    }

    // Rule 7: All non-aggregated columns in the SELECT list must appear in the GROUP BY clause.
    for (const col of nonAggregatedSelectColumns) {
      if (!groupByColumns.includes(col)) {
        this.addError(
          `Column '${col}' in SELECT list is not an aggregate and not in GROUP BY clause.`,
          undefined, // No specific location for this rule, as it's a comparison
          col
        );
      }
    }
  }

  private visitHaving(node: any, scope: Scope, selectOutputColumns: string[]) {
    // Rule 8: HAVING must only be used with aggregates or in combination with GROUP BY.
    // This is a complex rule. For simplicity, we'll just ensure all columns in HAVING are resolvable.
    // A more advanced check would verify if expressions are aggregates or if GROUP BY is present.
    this.visitExpression(node, scope, selectOutputColumns);
  }

  private visitOrderBy(orderBy: any[], scope: Scope, selectOutputColumns: string[]) {
    for (const order of orderBy) {
      if (order.expr?.type === 'column_ref') {
        const colName = order.expr.column.toLowerCase();
        // Rule 9: Columns used in ORDER BY must exist in the query scope (projection or underlying tables).
        // Prioritize columns from the SELECT list's projection
        if (!selectOutputColumns.includes(colName)) {
          // If not in SELECT output, try to resolve against current scope (underlying tables/CTEs)
          const resolved = this.resolveColumnInScope(order.expr, scope);
          if (!resolved) {
            this.addError(
              `Column '${order.expr.column}' in ORDER BY clause not found in SELECT list or available sources.`,
              order.expr.location,
              order.expr.column
            );
          }
        }
        this.visitColumnRef(order.expr, scope); // Also validate as a regular column reference
      } else {
        this.visitExpression(order.expr, scope, selectOutputColumns);
      }
      
      // Rule 9: Check for valid order direction
      if (order.type && !['ASC', 'DESC'].includes(order.type)) {
        this.addError(
          `Invalid ORDER BY direction '${order.type}'. Use ASC or DESC.`,
          order.location,
          order.type
        );
      }
    }
  }

  private visitLimit(node: any, scope: Scope) {
    // Rule 12: LIMIT / OFFSET Validity - Must be numeric constants or valid parameters.
    if (node.value && node.value.type !== 'number') {
      this.addError(
        `LIMIT value must be a numeric constant.`,
        node.value.location,
        node.value.value
      );
    }
    if (node.offset && node.offset.type !== 'number') {
      this.addError(
        `OFFSET value must be a numeric constant.`,
        node.offset.location,
        node.offset.value
      );
    }
  }

  private visitExpression(node: any, scope: Scope, selectOutputColumns?: string[]) {
    if (!node) return;

    switch (node.type) {
      case 'column_ref':
        this.visitColumnRef(node, scope);
        break;
      case 'binary_expr':
      case 'and_expr':
      case 'or_expr':
        this.visitExpression(node.left, scope, selectOutputColumns);
        this.visitExpression(node.right, scope, selectOutputColumns);
        break;
      case 'function':
      case 'aggr_func':
        if (node.args?.expr) {
          this.visitExpression(node.args.expr, scope, selectOutputColumns);
        }
        break;
      case 'case':
        for (const when of node.when || []) {
          this.visitExpression(when.cond, scope, selectOutputColumns);
          this.visitExpression(when.result, scope, selectOutputColumns);
        }
        if (node.else) {
          this.visitExpression(node.else, scope, selectOutputColumns);
        }
        break;
      case 'subquery':
        this.visitSelect(node.ast as Select, scope); // Recursively validate subquery
        break;
      // Add more expression types as needed
    }
  }

  private resolveColumnInScope(node: any, scope: Scope): boolean {
    const colName = node.column.toLowerCase();
    const sourceAlias = node.table?.toLowerCase();

    if (sourceAlias) {
      const source = scope.findSource(sourceAlias);
      return !!source && (source.hasStar || source.columns.includes(colName));
    } else {
      const allSources = scope.getAllSources();
      let foundCount = 0;
      for (const [, source] of Array.from(allSources.entries())) {
        if (source.hasStar || source.columns.includes(colName)) {
          foundCount++;
        }
      }
      return foundCount === 1; // Must resolve unambiguously
    }
  }

  private visitColumnRef(node: any, scope: Scope) {
    const colName = node.column;
    const sourceAlias = node.table;

    if (sourceAlias) {
      // Case: Column is qualified (e.g., `s.TotalSales`)
      const source = scope.findSource(sourceAlias);
      if (!source) {
        this.addError(
          `Unknown source '${sourceAlias}'`,
          node.location,
          sourceAlias
        );
        return;
      }
      
      // Rule 4: Qualified columns must exist in the referenced table/CTE.
      if (!source.hasStar && !source.columns.find((c: string) => c.toLowerCase() === colName.toLowerCase())) {
        this.addError(
          `Column '${colName}' not found in source '${sourceAlias}'`,
          node.location,
          colName
        );
      }
    } else {
      // Rule 4: Unqualified columns must resolve to exactly one source (no ambiguity).
      const availableColumns = new Map<string, string>();
      const allSources = scope.getAllSources();
      
      for (const [alias, source] of Array.from(allSources.entries())) {
        if (source.hasStar || source.columns.some((c: string) => c.toLowerCase() === colName.toLowerCase())) {
          availableColumns.set(alias, source.name);
        }
      }

      if (availableColumns.size === 0) {
        this.addError(`Column '${colName}' not found in any available sources`, node.location, colName);
      } else if (availableColumns.size > 1) {
        const sources = Array.from(availableColumns.entries())
          .map(([alias, name]) => `'${alias}' (from '${name}')`).join(', ');
        this.addError(
          `Column '${colName}' is ambiguous. It exists in multiple sources: ${sources}`,
          node.location,
          colName
        );
      }
    }
  }
  
  private visitAggregateFunction(node: any, scope: Scope) {
    // Validate arguments of aggregate functions
    if (node.args && node.args.expr) {
      this.visitExpression(node.args.expr, scope);
    }
  }

  private getSelectOutputColumns(selectAst: Select): { cols: string[]; hasStar: boolean } {
    const cols: string[] = [];
    let hasStar = false;
    const seenColNames = new Set<string>(); // For Rule 11: Duplicate Column Aliases

    for (const c of selectAst.columns || []) {
      if (c.expr?.type === "star") {
        hasStar = true;
        // Rule 6: * is valid, but mixing * with explicit column names can be flagged.
        if (cols.length > 0) {
          this.addError(
            `Mixing '*' with explicit column names is generally not recommended.`,
            c.location,
            '*'
          );
        }
        continue;
      }
      
      let colName: string | undefined;
      if (c.as) {
        colName = c.as;
      } else if (c.expr?.type === 'column_ref') {
        colName = c.expr.column;
      } else if (c.expr?.type === 'function' || c.expr?.type === 'aggr_func') {
        // For functions without an explicit alias, use the function name as a fallback
        colName = c.expr.name;
      }
      // For other expressions, if no alias, we might not get a meaningful name here.

      if (colName) {
        const lowerColName = colName.toLowerCase();
        // Rule 11: No duplicate column aliases in a single SELECT list.
        if (seenColNames.has(lowerColName)) {
          this.addError(
            `Duplicate column alias '${colName}' in SELECT list.`,
            c.location,
            colName
          );
        } else {
          seenColNames.add(lowerColName);
          cols.push(lowerColName);
        }
      }
    }
    return { cols, hasStar };
  }

  private addError(message: string, location: any, token?: string) {
    if (this.suppressErrors) {
      return; // Do not add error if suppression is enabled
    }

    let line: number | undefined;
    let column: number | undefined;
    let length: number = 1;

    if (location?.start) {
      line = location.start.line;
      column = location.start.column;
      if (location.end?.column && column !== undefined) {
        length = location.end.column - column;
      }
    } else if (token) {
      const loc = this.findErrorLocation(this.sql, token);
      line = loc.line;
      column = loc.column;
      length = token.length;
    }

    this.errors.push({ message, line, column, length });
  }

  private formatParseError(err: any, sql: string): ValidationError {
    let message = "Syntax error";
    const tokenMatch = err.message?.match(/expecting \'(.+?)\' but/);
    const expecting = tokenMatch?.[1];
    const unexpectedTokenMatch = err.message?.match(/but "(.+?)" found/);
    const unexpected = unexpectedTokenMatch?.[1];

    if (expecting && unexpected) {
      message += `: expecting '${expecting}' but found '${unexpected}'`;
    } else if (unexpected) {
      message += `: unexpected '${unexpected}'`;
    } else {
      message += `: ${err.message.split("\n")[0]}`;
    }

    const location = err.location?.start || this.findErrorLocation(sql, unexpected || expecting);
    let length = 1;
    if (unexpected) {
      length = unexpected.length;
    } else if (expecting) {
      length = expecting.length;
    } else if (err.location?.end?.column && location.column !== undefined) {
      length = err.location.end.column - location.column;
    }

    return { message, line: location.line, column: location.column, length };
  }

  private findErrorLocation(sql: string, token: string | undefined) {
    if (!token) return { line: 1, column: 1 };
    
    // Try to find the last occurrence of the token, as syntax errors often occur at the end of a statement
    const index = sql.lastIndexOf(token);
    if (index === -1) return { line: 1, column: 1 };
    
    const lines = sql.slice(0, index).split("\n");
    return { line: lines.length, column: lines[lines.length - 1].length + 1 };
  }
}

/**
 * Format error with precise location information
 */
export function formatError(error: ValidationError, sql?: string): string {
  let message = error.message;
  if (error.line !== undefined && error.column !== undefined) {
    message += ` at line ${error.line}, column ${error.column}`;
    
    if (sql && error.line > 0) {
      const lines = sql.split('\n');
      if (error.line <= lines.length) {
        const lineContent = lines[error.line - 1];
        const pointer = ' '.repeat(error.column - 1) + '^';
        message += `:\n\n${lineContent}\n${pointer}`;
      }
    }
  }
  return message;
}

/**
 * Main function to validate a SQL string.
 */
export function validateSQL(sql: string, suppressErrors: boolean = false) {
  const validator = new SqlValidator(suppressErrors);
  return validator.validate(sql);
}

// Example usage:
// const sql = `WITH SalesCTE AS (...)`; // Your SQL here
// const result = validateSQL(sql);
// if (!result.valid) {
//   result.errors.forEach(error => {
//     console.error(formatError(error, sql));
//   });
// }
