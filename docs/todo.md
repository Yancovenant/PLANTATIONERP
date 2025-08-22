# INPHMS ERP - Development TODO

## [BLOCKED] Current Status: CLI Framework Only
**What you have:** Command-line interface that can't start a web server
**What you need:** Complete ERP system that runs as .exe with MariaDB

---

## [WIP] Phase 1: Fix Broken Server (HIGH PRIORITY)
**Goal:** Make the server actually start without crashing

### 1.1 Fix server.py circular imports
- **File:** `inphms/cli/server.py`
- **Problem:** Function calls undefined functions
- **Fix:** Complete the `main()` function
- **Status:** [ ] Not Started

### 1.2 Create missing functions
- **Missing:** `setup_pid_file()`, `export_translation()`, `import_translation()`
- **Fix:** Implement these functions or remove calls
- **Status:** [ ] Not Started

### 1.3 Remove Odoo references
- **Problem:** Code still references `odoo.service.db` instead of Inphms
- **Fix:** Replace with Inphms equivalents
- **Status:** [ ] Not Started

---

## [ ] Phase 2: Build Web Server (HIGH PRIORITY)
**Goal:** Create actual HTTP server using Werkzeug

### 2.1 Create web application structure
- **File:** `inphms/web/__init__.py`
- **Purpose:** Flask-like app factory
- **Status:** [ ] Not Started

### 2.2 Implement basic routes
- **File:** `inphms/web/routes.py`
- **Purpose:** Homepage, basic ERP pages
- **Status:** [ ] Not Started

### 2.3 Add Jinja2 templates
- **File:** `inphms/web/templates/base.html`
- **Purpose:** Base template for ERP interface
- **Status:** [ ] Not Started

---

## [ ] Phase 3: Database Layer (MEDIUM PRIORITY)
**Goal:** Connect to MariaDB and create basic models

### 3.1 Database connection
- **File:** `inphms/database/__init__.py`
- **Purpose:** MariaDB connection management
- **Status:** [ ] Not Started

### 3.2 Basic ORM system
- **File:** `inphms/database/models.py`
- **Purpose:** Simple model definitions
- **Status:** [ ] Not Started

### 3.3 First ERP model
- **File:** `inphms/addons/base/models/user.py`
- **Purpose:** User management system
- **Status:** [ ] Not Started

---

## [ ] Phase 4: ERP Modules (MEDIUM PRIORITY)
**Goal:** Create actual business logic modules

### 4.1 Base module
- **File:** `inphms/addons/base/__init__.py`
- **Purpose:** Core ERP functionality
- **Status:** [ ] Not Started

### 4.2 Plantation module
- **File:** `inphms/addons/plantation/__init__.py`
- **Purpose:** Plantation-specific features
- **Status:** [ ] Not Started

### 4.3 User interface
- **File:** `inphms/addons/web/__init__.py`
- **Purpose:** Web interface components
- **Status:** [ ] Not Started

---

## [ ] Phase 5: Executable Build (HIGH PRIORITY)
**Goal:** Create single .exe file for client deployment

### 5.1 Install PyInstaller
```bash
pip install pyinstaller
```
**Status:** [ ] Not Started

### 5.2 Create build script
- **File:** `build_exe.bat`
- **Purpose:** One-click executable creation
- **Status:** [ ] Not Started

### 5.3 Test executable
- **Goal:** Verify .exe works on clean Windows machine
- **Status:** [ ] Not Started

---

## [ ] Phase 6: MariaDB Integration (MEDIUM PRIORITY)
**Goal:** Connect to XAMPP MariaDB

### 6.1 Update requirements.txt
- **Add:** `pymysql` for MariaDB connection
- **Status:** [ ] Not Started

### 6.2 Database configuration
- **File:** `config/database.conf`
- **Purpose:** MariaDB connection settings
- **Status:** [ ] Not Started

### 6.3 Test database connection
- **Goal:** Verify ERP can connect to MariaDB
- **Status:** [ ] Not Started

---

## [ ] Phase 7: Testing & Deployment (LOW PRIORITY)
**Goal:** Ensure everything works before client delivery

### 7.1 Unit tests
- **File:** `inphms/tests/`
- **Purpose:** Test core functionality
- **Status:** [ ] Not Started

### 7.2 Integration tests
- **Goal:** Test complete ERP workflow
- **Status:** [ ] Not Started

### 7.3 Client deployment package
- **Contents:** .exe + config files + database setup
- **Status:** [ ] Not Started

---

## IMMEDIATE NEXT STEPS (Do These First):

1. **Fix server.py** - Make it start without crashing
2. **Create basic web server** - Get HTTP working
3. **Test with simple page** - Verify web interface works
4. **Build .exe** - Create single executable
5. **Test deployment** - Verify client can run it

---

## DEPENDENCIES TO INSTALL:
```bash
pip install werkzeug jinja2 pymysql
pip install pyinstaller  # For .exe creation
```

## FILES TO CREATE:
- `inphms/web/__init__.py`
- `inphms/web/routes.py`
- `inphms/web/templates/base.html`
- `inphms/database/__init__.py`
- `build_exe.bat`

## CURRENT BLOCKERS:
- Server won't start due to missing functions
- No web server implementation
- No database connection
- No ERP modules

**Next Action:** Start with Phase 1.1 - Fix the broken server.py file
