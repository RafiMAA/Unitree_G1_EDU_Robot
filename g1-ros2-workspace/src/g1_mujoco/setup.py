from setuptools import find_packages, setup

package_name = 'g1_mujoco'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/g1_mujoco']),
        ('share/g1_mujoco', ['package.xml']),
        ('share/g1_mujoco/launch', ['launch/sim.launch.py']),        
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
            "mujoco_bridge = g1_mujoco.mujoco_bridge_node:main",
        ],
    },
)
