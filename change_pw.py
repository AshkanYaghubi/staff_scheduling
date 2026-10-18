import sys
from app import app, db, AppAdmin
from werkzeug.security import generate_password_hash

def change_password(username, new_password):
    with app.app_context():
        # Look up the admin user
        admin = AppAdmin.query.filter_by(username=username).first()
        if not admin:
            print(f"Error: Admin user '{username}' not found.")
            sys.exit(1)
        
        # Hash and update the password
        admin.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')
        db.session.commit()
        print(f"Success: Password for admin user '{username}' has been updated.")

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python change_admin_pass.py <username> <new_password>")
        sys.exit(1)
    
    target_username = sys.argv[1]
    target_password = sys.argv[2]
    change_password(target_username, target_password)