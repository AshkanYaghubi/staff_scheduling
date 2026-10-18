# reset_supervisor_passwords.py
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash
from app import app, db, AuthEmployee  

def reset_supervisor_passwords(new_password="default123"):
    """Reset password for all supervisors (USE WITH CAUTION)"""
    with app.app_context():
        supervisors = AuthEmployee.query.filter_by(role='supervisor').all()
        
        for sup in supervisors:
            sup.password_hash = generate_password_hash(new_password, method='scrypt')
        
        db.session.commit()
        print(f"Reset passwords for {len(supervisors)} supervisors")

def reset_specific_supervisors(employee_numbers, new_password):
    """Reset passwords for specific supervisors"""
    with app.app_context():
        supervisors = AuthEmployee.query.filter(
            AuthEmployee.employee_number.in_(employee_numbers),
            AuthEmployee.role == 'supervisor'
        ).all()
        
        for sup in supervisors:
            sup.password_hash = generate_password_hash(new_password, method='scrypt')
        
        db.session.commit()
        print(f"Reset passwords for {len(supervisors)} supervisors")

if __name__ == "__main__":
    
    reset_supervisor_passwords("Medici1230")
