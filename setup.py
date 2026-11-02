from setuptools import setup, find_packages

setup(
    name="veritas",
    version="1.0.0",
    description="VERITAS Industrial Data Intelligence Platform",
    author="VERITAS Team",
    packages=find_packages(),
    python_requires=">=3.12",
    install_requires=[
        "pandas>=2.1.0",
        "numpy>=1.26.0",
        "scipy>=1.11.0",
        "pyarrow>=14.0.0",
        "scikit-learn>=1.3.0",
        "anthropic>=0.23.0",
        "openai>=1.10.0",
        "pydantic>=2.5.0",
        "redis>=5.0.0",
        "prometheus-client>=0.19.0",
        "requests>=2.31.0",
        "cryptography>=41.0.0",
    ],
    entry_points={
        "console_scripts": [
            "veritas=veritas.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Manufacturing",
        "Topic :: Scientific/Engineering :: Industrial",
        "Programming Language :: Python :: 3.10",
    ],
)
