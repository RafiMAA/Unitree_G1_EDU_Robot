from setuptools import find_packages, setup

package_name = 'g1_core'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='abdul-rafi',
    maintainer_email='rafiabdul7128@gmail.com',
    description='TODO: Package description',
    license='TODO: License declaration',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'state_machine = g1_core.state_machine_node:main',
            'teleop_keyboard = g1_core.teleop_keyboard:main',
        ],
    },
)
