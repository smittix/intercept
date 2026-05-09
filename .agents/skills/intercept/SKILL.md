```markdown
# intercept Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill teaches you the core development patterns and conventions used in the `intercept` Python repository. You'll learn about file organization, import/export styles, commit message habits, and how to write and run tests in this codebase. These patterns ensure consistency and maintainability across the project.

## Coding Conventions

### File Naming
- Use **snake_case** for all file names.
  - **Example:** `data_processor.py`, `user_utils.py`

### Import Style
- Use **relative imports** within the package.
  - **Example:**
    ```python
    from .helpers import parse_data
    from ..models import User
    ```

### Export Style
- Use **named exports** (explicitly define what is exported).
  - **Example:**
    ```python
    __all__ = ['parse_data', 'User']
    ```

### Commit Messages
- Freeform, no strict prefix required.
- Average length: ~56 characters.
  - **Example:**  
    ```
    Fix bug in data parsing when input is empty
    ```

## Workflows

### Adding a New Module
**Trigger:** When you need to add new functionality.
**Command:** `/add-module`

1. Create a new Python file using snake_case naming.
2. Implement your functionality.
3. Use relative imports for any internal dependencies.
4. Define `__all__` for named exports.
5. Write corresponding tests in a `*.test.*` file.

### Running Tests
**Trigger:** When you want to verify code correctness.
**Command:** `/run-tests`

1. Identify test files matching the `*.test.*` pattern.
2. Run tests using the project's preferred test runner (framework unknown; try `pytest` or `unittest`).
   - **Example:**
     ```bash
     pytest
     ```
     or
     ```bash
     python -m unittest discover
     ```

### Refactoring Imports
**Trigger:** When reorganizing code or resolving import issues.
**Command:** `/refactor-imports`

1. Update imports to use relative paths within the package.
2. Ensure all modules use snake_case naming.
3. Test to confirm imports resolve correctly.

## Testing Patterns

- Test files follow the `*.test.*` naming pattern.
  - **Example:** `user_utils.test.py`, `data_processor.test.py`
- The testing framework is not explicitly defined; try using `pytest` or `unittest`.
- Place tests alongside or near the modules they cover.

## Commands
| Command        | Purpose                                      |
|----------------|----------------------------------------------|
| /add-module    | Add a new module following conventions       |
| /run-tests     | Run all test files in the repository         |
| /refactor-imports | Refactor imports to use relative style    |
```
