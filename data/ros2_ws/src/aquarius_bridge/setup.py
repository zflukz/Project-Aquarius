from glob import glob

from setuptools import setup

package_name = "aquarius_bridge"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", glob("launch/*.py")),
        ("share/" + package_name + "/config", glob("config/*.yaml")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Aquarius team",
    maintainer_email="team@aquarius.local",
    description="ROS 2 to Kafka bridge for the Aquarius multi-UAV search system",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "bridge = aquarius_bridge.bridge_node:main",
        ],
    },
)
