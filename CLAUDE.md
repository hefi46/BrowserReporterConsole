# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands


### Running the Application
```bash
# Start the full stack (PostgreSQL + FastAPI backend)
sudo docker compose up -d

# View logs
docker compose logs -f backend
docker compose logs -f db

# Stop the application
docker compose down
```

### Development Mode
```bash
# Run backend directly (requires PostgreSQL running separately)
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Generate mock data for testing
python generate_mock_data.py
```

### Database Operations
```bash
# Access PostgreSQL directly
docker exec -it $(docker compose ps -q db) psql -U browser_guardian -d browser_guardian

# Reset database (removes all data)
docker compose down -v
docker compose up -d
```

### Testing & Quality
```bash
# No automated testing framework configured
# Manual testing via web interface at http://localhost:8000
# API testing can be done via browser or curl commands
```

## Architecture Overview

### Core Components
- **FastAPI Backend** (`backend/main.py`): Main application with API endpoints and web interface
- **PostgreSQL Database**: Persistent data storage with AsyncPG driver
- **Session-based Authentication**: No JWT tokens, uses server-side sessions
- **Bootstrap Frontend**: Server-rendered HTML with minimal JavaScript

### Data Model Architecture
- **DashboardUser**: Admin panel users with role-based access (admin/user roles)
- **User**: Browsing data subjects with homegroup associations
- **Visit**: Individual website visits with timestamps and URL data
- **Homegroups**: Logical groupings (3A, 4A, 5A, 6C) for user organization

### Key Modules
- `backend/database.py`: SQLAlchemy async engine and session management
- `backend/models.py`: Database models using SQLAlchemy ORM
- `backend/schemas.py`: Pydantic models for API validation
- `backend/crud.py`: Database operations and business logic
- `backend/utils.py`: Security utilities for config encryption

### API Architecture
- **Authentication Routes**: `/login`, `/logout`, `/api/auth/user`
- **Data Ingestion**: `/api/reports/data` (requires API key in headers)
- **Analytics**: `/api/reports/all`, `/api/reports/user/{username}`
- **Admin Management**: `/api/admin/users/*` (role-based access control)

### Security Model
- Session-based authentication with encrypted cookies
- Role-based access control (admin vs user permissions)
- API key protection for data ingestion endpoints
- bcrypt password hashing for dashboard users
- CORS enabled for browser extension integration

### Performance Considerations
- Server-side pagination implemented for large datasets (v2.1)
- Async/await pattern throughout for database operations
- Bulk insert operations for visit data ingestion
- Export functionality handles large datasets efficiently

## Environment Configuration

Required environment variables:
- `DATABASE_URL`: PostgreSQL connection string  
- `SESSION_SECRET`: Session encryption key (auto-generated if not set)

Default database credentials (development):
- Username: `browser_guardian`
- Password: `browser_guardian`
- Database: `browser_guardian`

Default admin credentials:
- Username: `admin`
- Password: `admin` (should be changed immediately)

## Common Development Patterns

### Adding New API Endpoints
1. Define Pydantic schema in `schemas.py`
2. Add database operations in `crud.py`
3. Implement endpoint in `main.py` with proper authentication
4. Use dependency injection for database sessions

### Database Schema Changes
1. Modify models in `models.py`
2. Database migrations are handled by restarting containers (development)
3. For production, implement proper Alembic migrations

### Authentication Requirements
- Dashboard routes require valid session
- Admin routes check for admin role
- API endpoints use header-based API key authentication
- Use `get_current_user()` dependency for protected routes

### Frontend Development
- Templates use Jinja2 with Bootstrap 5 styling
- Static files served from `backend/static/`
- No build process required - edit HTML/CSS/JS directly
- Dashboard features: pagination controls, search, filtering, export

### Data Management
- CSV bulk import format: `username,password,role`
- Export functionality supports large datasets with proper streaming
- Mock data generator creates realistic browsing patterns
- Homegroup filtering: 3A, 4A, 5A, 6C organizational groups
