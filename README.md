# Browser Extension Dashboard (BRV2)

A comprehensive dashboard system for collecting and analyzing browsing history data from browser extensions, built with FastAPI and PostgreSQL.

## Features

### User Management
- **Admin Panel**: Complete user management interface with role-based access control
- **CRUD Operations**: Create, read, update, and delete dashboard users
- **Bulk CSV Import**: Import multiple users at once via CSV upload
- **Role-Based Security**: Admin and regular user roles with appropriate permissions

### Data Collection & Analysis
- **Browser Extension Integration**: Secure API endpoints for browser extension data collection
- **Real-time Dashboard**: Bootstrap-based responsive dashboard interface
- **User Analytics**: Track browsing patterns, visit counts, and activity across homegroups
- **Filtering & Search**: Advanced filtering by homegroup, activity period, and search terms

### Security & Authentication
- **Session-based Authentication**: Secure login/logout system
- **API Key Protection**: Protected data ingestion endpoints
- **Role-based Access Control**: Admin-only functions with proper authorization
- **Password Security**: Hashed password storage with bcrypt

## Tech Stack

- **Backend**: FastAPI (Python)
- **Database**: PostgreSQL with SQLAlchemy ORM
- **Frontend**: Bootstrap 5, HTML5, JavaScript
- **Authentication**: Session-based with secure cookies
- **Containerization**: Docker & Docker Compose

## Quick Start

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd BRV2
   ```

2. **Start the application:**
   ```bash
   docker-compose up -d
   ```

3. **Access the dashboard:**
   - Open http://localhost:8000
   - Login with default admin credentials: `admin` / `admin`

4. **Configure your browser extension:**
   - API Endpoint: `http://localhost:8000/api/reports/data`
   - API Key: `your-secure-api-key-here` (change in environment)

## Project Structure

```
BRV2/
├── backend/
│   ├── main.py              # FastAPI application & API endpoints
│   ├── database.py          # Database configuration & models
│   ├── schemas.py           # Pydantic models for API validation
│   ├── crud.py              # Database operations
│   └── templates/
│       ├── dashboard.html   # Main dashboard interface
│       └── login.html       # Login page
├── docker-compose.yml       # Container orchestration
├── Dockerfile              # Backend container definition
├── requirements.txt        # Python dependencies
├── generate_mock_data.py   # Development data generator
└── README.md
```

## API Endpoints

### Authentication
- `GET /login` - Login page
- `POST /login` - Authenticate user
- `GET /logout` - Logout user
- `GET /api/auth/user` - Get current user info

### Data Collection
- `POST /api/reports/data` - Ingest browsing data (API key required)

### Reports & Analytics
- `GET /api/reports/all` - Get all user analytics
- `GET /api/reports/user/{username}` - Get specific user data

### Admin Management
- `GET /api/admin/users` - List dashboard users
- `POST /api/admin/users` - Create new user
- `PUT /api/admin/users/{username}` - Update user
- `DELETE /api/admin/users/{username}` - Delete user
- `POST /api/admin/users/bulk-import` - CSV bulk import
- `GET /api/admin/users/example-csv` - Download CSV template

## CSV Bulk Import Format

```csv
username,password,role
user1,password123,user
admin2,securepass456,admin
```

**Requirements:**
- Username: 3-50 characters, alphanumeric and underscores only
- Password: 6-100 characters
- Role: Either "admin" or "user"

## Environment Variables

- `DATABASE_URL`: PostgreSQL connection string
- `API_KEY`: Secure API key for data collection endpoints
- `SECRET_KEY`: Session encryption key

## Development

### Mock Data Generation
Generate test data with realistic browsing patterns:
```bash
python generate_mock_data.py
```

### Database Schema
The application uses SQLAlchemy models for:
- **Users**: Browsing data users with homegroups
- **Visits**: Individual website visits with timestamps
- **DashboardUsers**: Admin panel users with roles

## Current Data Summary
- **Dashboard Users**: 6 total (including admins and bulk imported)
- **Browsing Users**: 22 across 4 homegroups (3A, 4A, 5A, 6C)
- **Total Visits**: 514 with realistic browsing patterns
- **Homegroups**: 3A, 4A, 5A, 6C with varying activity levels

## Security Notes

1. Change default admin password immediately
2. Update API_KEY in production environment
3. Use HTTPS in production
4. Review user permissions regularly
5. Monitor API usage and access logs

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

This project is for educational/internal use. Please review and add appropriate licensing as needed.
