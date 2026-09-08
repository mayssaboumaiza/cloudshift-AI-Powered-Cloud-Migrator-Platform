"""
setup.py — Package configuration for cloud-migrator.

Replaces sys.path.insert() hacks with proper package installation.
Allows: pip install -e .
"""
from setuptools import setup, find_packages

with open("requirements.txt", "r", encoding="utf-8") as f:
    requirements = [line.strip() for line in f if line.strip() and not line.startswith("#")]

setup(
    name="cloud-migrator",
    version="0.1.0",
    description="AI-powered bi-directional cloud migration tool using LangGraph & ReAct agents",
    author="Mayssa Boumaiza",
    author_email="mayssa@talan.tn",
    packages=find_packages(exclude=["tests", "tests.*"]),
    python_requires=">=3.11",
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "cloud-migrator=app:main",
        ],
    },
    include_package_data=True,
    package_data={
        "agents": ["templates/*.j2"],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Libraries :: Application Frameworks",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
    ],
)
