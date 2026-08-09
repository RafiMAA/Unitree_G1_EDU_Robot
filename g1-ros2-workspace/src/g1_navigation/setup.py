import os
from glob import glob

from setuptools import find_packages, setup


package_name = 'g1_navigation'


setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml', 'README.md']),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
        (os.path.join('share', package_name, 'maps'), glob('maps/*')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='abdul-rafi',
    maintainer_email='rafiabdul7128@gmail.com',
    description='2D SLAM, Nav2, 3D LiDAR avoidance and web control for G1',
    license='Apache-2.0',
    extras_require={'test': ['pytest']},
    entry_points={
        'console_scripts': [
            'cloud_to_scan = g1_navigation.cloud_to_scan:main',
            'cloud_filter = g1_navigation.cloud_filter:main',
            'command_mux = g1_navigation.command_mux:main',
            'web_gateway = g1_navigation.web_gateway:main',
        ],
    },
)
